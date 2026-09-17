"""
Two MediaPipe APIs, one interface.

MediaPipe has two generations of face-mesh API and which one you get
depends on the version installed:

  solutions   `mp.solutions.face_mesh.FaceMesh`. The original. No model
              file to fetch, `refine_landmarks` switches between 468 and
              478 points, and it is what the scripts facekit replaces
              used. Deprecated upstream, and absent from some 0.10.x
              builds.
  tasks       `mediapipe.tasks.python.vision.FaceLandmarker`. The
              current one. Always returns 478 landmarks, and needs a
              .task bundle downloaded once.

Both are wrapped to return the same thing: a list of faces per frame,
each face a list of (x, y, z) in normalised coordinates. Everything
downstream is identical, so a landmark table does not record which
backend produced it — except that `REFINE_LANDMARKS` has no effect on
the tasks backend, which is noted in the run log rather than silently
ignored.

The solutions backend is preferred when both are available, because it
reproduces earlier output exactly and needs no download.
"""

from __future__ import annotations

from pathlib import Path

from .. import settings as st

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/1/face_landmarker.task"
)
MODEL_DIR = st.CONFIG_DIR / "models"
MODEL_FILE = MODEL_DIR / "face_landmarker.task"


def available() -> dict:
    """Which backends this installation can use."""
    try:
        import mediapipe as mp
    except ImportError:
        return {"solutions": False, "tasks": False, "mediapipe": None,
                "error": "mediapipe is not installed"}

    version = getattr(mp, "__version__", "unknown")
    solutions = False
    tasks = False

    try:
        solutions = hasattr(mp.solutions.face_mesh, "FaceMesh")
    except (AttributeError, ImportError):
        solutions = False

    try:
        from mediapipe.tasks.python import vision

        tasks = hasattr(vision, "FaceLandmarker")
    except (AttributeError, ImportError):
        tasks = False

    return {"solutions": solutions, "tasks": tasks, "mediapipe": version,
            "error": None if (solutions or tasks) else
            f"mediapipe {version} exposes neither the solutions nor the "
            f"tasks face-mesh API"}


def choose(preference: str = "auto") -> str:
    """The backend name to use, or raise with why not."""
    have = available()
    if preference == "solutions":
        if not have["solutions"]:
            raise RuntimeError(
                f"mediapipe {have['mediapipe']} has no solutions.face_mesh. "
                f"Set Mesh backend to auto or tasks.")
        return "solutions"
    if preference == "tasks":
        if not have["tasks"]:
            raise RuntimeError(
                f"mediapipe {have['mediapipe']} has no tasks FaceLandmarker. "
                f"Set Mesh backend to auto or solutions.")
        return "tasks"

    if have["solutions"]:
        return "solutions"
    if have["tasks"]:
        return "tasks"
    raise RuntimeError(have["error"] or "no usable mediapipe face-mesh API")


def ensure_model(log=lambda m: None) -> Path:
    """The .task bundle, downloaded once if it is not already here."""
    if MODEL_FILE.exists() and MODEL_FILE.stat().st_size > 1000:
        return MODEL_FILE

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    log(f"  fetching the face landmarker model, about 3 MB, once")
    log(f"  {MODEL_URL}")
    try:
        import urllib.request

        urllib.request.urlretrieve(MODEL_URL, MODEL_FILE)
    except Exception as exc:
        raise RuntimeError(
            f"Could not download the face landmarker model ({exc}). This is "
            f"the only step in facekit that needs a network connection. "
            f"Download it yourself from\n  {MODEL_URL}\nand save it as\n"
            f"  {MODEL_FILE}") from exc
    log(f"  saved to {MODEL_FILE}")
    return MODEL_FILE


# ======================================================================
#  Solutions backend
# ======================================================================

class SolutionsMesh:
    """The legacy mp.solutions.face_mesh wrapper."""

    name = "solutions"

    def __init__(self, values: dict):
        import mediapipe as mp

        self._mp = mp
        self._values = values
        self._mesh = mp.solutions.face_mesh.FaceMesh(
            static_image_mode=False,
            max_num_faces=values["MAX_FACES"],
            refine_landmarks=values["REFINE_LANDMARKS"],
            min_detection_confidence=values["MIN_DETECTION_CONFIDENCE"],
            min_tracking_confidence=values["MIN_TRACKING_CONFIDENCE"],
        )

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def close(self):
        self._mesh.close()

    @property
    def n_landmarks(self) -> int:
        return st.num_landmarks(self._values)

    def process(self, rgb, frame_index: int) -> list:
        results = self._mesh.process(rgb)
        if not results.multi_face_landmarks:
            return []
        return [[(lm.x, lm.y, lm.z) for lm in face.landmark]
                for face in results.multi_face_landmarks]

    def draw(self, image, face: list) -> None:
        """Mesh tesselation, contours and irises, in MediaPipe's own styles."""
        mp = self._mp
        from mediapipe.framework.formats import landmark_pb2

        proto = landmark_pb2.NormalizedLandmarkList()
        for x, y, z in face:
            proto.landmark.add(x=x, y=y, z=z)

        styles = mp.solutions.drawing_styles
        for attr, style in (
            ("FACEMESH_TESSELATION", "get_default_face_mesh_tesselation_style"),
            ("FACEMESH_CONTOURS", "get_default_face_mesh_contours_style"),
            ("FACEMESH_IRISES", "get_default_face_mesh_iris_connections_style"),
        ):
            connections = getattr(mp.solutions.face_mesh, attr, None)
            if connections is None:
                continue
            mp.solutions.drawing_utils.draw_landmarks(
                image=image,
                landmark_list=proto,
                connections=connections,
                landmark_drawing_spec=None,
                connection_drawing_spec=getattr(styles, style)(),
            )


# ======================================================================
#  Tasks backend
# ======================================================================

class TasksMesh:
    """The current FaceLandmarker wrapper.

    Runs in VIDEO mode, which keeps the tracker's frame-to-frame state
    the same way the solutions backend did. That needs a monotonically
    increasing timestamp per frame, derived here from the frame index
    and the recording's frame rate.
    """

    name = "tasks"

    def __init__(self, values: dict, fps: float = 30.0,
                 log=lambda m: None):
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision

        model = ensure_model(log)
        self._values = values
        self._fps = fps or 30.0
        self._vision = vision
        options = vision.FaceLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(model)),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=values["MAX_FACES"],
            min_face_detection_confidence=values["MIN_DETECTION_CONFIDENCE"],
            min_face_presence_confidence=values["MIN_DETECTION_CONFIDENCE"],
            min_tracking_confidence=values["MIN_TRACKING_CONFIDENCE"],
        )
        self._landmarker = vision.FaceLandmarker.create_from_options(options)
        self._connections = self._collect_connections(vision)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def close(self):
        self._landmarker.close()

    @property
    def n_landmarks(self) -> int:
        # The bundle always returns the refined set, irises included.
        return 478

    @staticmethod
    def _collect_connections(vision) -> list:
        """Every connection pair the landmarker exposes, deduplicated."""
        holder = getattr(vision, "FaceLandmarksConnections", None)
        if holder is None:
            return []
        pairs = set()
        for attr in dir(holder):
            if not attr.startswith("FACE_LANDMARKS"):
                continue
            value = getattr(holder, attr)
            if not isinstance(value, (list, tuple, frozenset, set)):
                continue
            for item in value:
                start = getattr(item, "start", None)
                end = getattr(item, "end", None)
                if start is None and isinstance(item, (tuple, list)) and len(item) == 2:
                    start, end = item
                if start is not None and end is not None:
                    pairs.add((int(start), int(end)))
        return sorted(pairs)

    def process(self, rgb, frame_index: int) -> list:
        import mediapipe as mp

        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        timestamp_ms = int(frame_index * 1000.0 / self._fps)
        result = self._landmarker.detect_for_video(image, timestamp_ms)
        if not result.face_landmarks:
            return []
        return [[(lm.x, lm.y, lm.z) for lm in face]
                for face in result.face_landmarks]

    def draw(self, image, face: list) -> None:
        """The tesselation, drawn directly.

        The tasks API ships no drawing helpers, so this is plain lines
        between connected points. It is thinner than the solutions
        render but serves the same purpose: checking that the mesh sits
        on the face.
        """
        import cv2

        height, width = image.shape[:2]
        points = [(int(x * width), int(y * height)) for x, y, _ in face]
        for start, end in self._connections:
            if start < len(points) and end < len(points):
                cv2.line(image, points[start], points[end],
                         (120, 190, 120), 1, cv2.LINE_AA)
        for point in points:
            cv2.circle(image, point, 1, (80, 140, 230), -1)


def open_mesh(values: dict, fps: float = 30.0, log=lambda m: None):
    """The right backend for this installation and these settings."""
    backend = choose(values.get("MESH_BACKEND", "auto"))
    if backend == "solutions":
        return SolutionsMesh(values)
    return TasksMesh(values, fps=fps, log=log)
