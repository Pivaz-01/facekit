"""
Stage 1: 468 or 478 facial landmarks per frame, from a folder of recordings.

Scans the configured folders, runs MediaPipe Face Mesh over every video
found, and writes one row per landmark per frame. Optionally renders the
mesh back over the recording, or onto black so the output can be shared
without showing the participant.

Rows are streamed to disk as they are produced rather than accumulated,
because a ten-minute recording at 478 landmarks is around nine million
rows and holding a batch of those in memory is how the original script
ran out of it.
"""

from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Callable, Iterable

from . import settings as st

CSV_HEADER = [
    "folder", "video", "frame", "face_id", "landmark_id",
    "x_norm", "y_norm", "z_norm", "x_pixel", "y_pixel",
]


def _noop(msg: str) -> None:
    pass


# ======================================================================
#  Dependencies
# ======================================================================

def requirements() -> dict:
    """What this stage needs, and whether it is there.

    Having mediapipe installed is not sufficient: some 0.10.x builds
    ship neither face-mesh API, and an import check alone would report
    the stage ready and then fail on the first frame.
    """
    from .mesh import available

    missing = []
    try:
        __import__("cv2")
    except ImportError:
        missing.append("opencv-python")

    have = available()
    if have["mediapipe"] is None:
        missing.append("mediapipe")
        note = ""
    elif not (have["solutions"] or have["tasks"]):
        note = have["error"]
    else:
        note = ""

    backends = [name for name in ("solutions", "tasks") if have.get(name)]
    return {
        "stage": "landmarks",
        "ok": not missing and not note,
        "missing": missing,
        "install": f"pip install {' '.join(missing)}" if missing else "",
        "note": note,
        "backends": backends,
        "mediapipe": have["mediapipe"],
    }


def _import_backends():
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "Landmark extraction needs opencv-python. "
            "Run `python -m facekit env` for the exact install command."
        ) from exc
    return cv2, np


# ======================================================================
#  Finding inputs
# ======================================================================

def find_videos(folder: Path, values: dict) -> list[Path]:
    """Every video under `folder`, excluding anything facekit rendered.

    Extension matching is case-insensitive, and the result is
    deduplicated: on a case-insensitive filesystem *.mp4 and *.MP4 match
    the same file.
    """
    wanted = {e.lower() if e.startswith(".") else f".{e.lower()}"
              for e in values["VIDEO_EXTENSIONS"]}
    generated = st.all_generated_suffixes()
    found = set()
    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in wanted:
            continue
        if any(path.stem.endswith(s) for s in generated):
            continue
        found.add(path)
    return sorted(found)


# ======================================================================
#  Rendering
# ======================================================================

def _open_writer(out_path: Path, fps: float, width: int, height: int,
                 preferred: str, log: Callable[[str], None]):
    """A VideoWriter using the preferred codec, falling back to mp4v.

    Returns (writer, codec) or (None, None).
    """
    import cv2

    candidates = [preferred]
    if preferred != "mp4v":
        candidates.append("mp4v")

    for code in candidates:
        fourcc = cv2.VideoWriter_fourcc(*code)
        writer = cv2.VideoWriter(str(out_path), fourcc, fps, (width, height))
        if writer.isOpened():
            if code != preferred:
                log(f"      codec {preferred} unavailable, using {code} "
                    f"(will not play in a browser)")
            return writer, code
        writer.release()
    return None, None


def _pick_principal_face(faces: Iterable):
    """The face with the largest bounding box."""
    faces = list(faces)
    if len(faces) == 1:
        return faces[0]
    best, best_area = None, -1.0
    for face in faces:
        xs = [point[0] for point in face]
        ys = [point[1] for point in face]
        area = (max(xs) - min(xs)) * (max(ys) - min(ys))
        if area > best_area:
            best, best_area = face, area
    return best


# ======================================================================
#  One video
# ======================================================================

def process_video(video_path: Path, mesh, sink, values: dict,
                  log: Callable[[str], None] = _noop) -> dict:
    """Run one recording. Writes rows through `sink`, returns a summary."""
    cv2, np = _import_backends()

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        log(f"    WARNING cannot open {video_path.name}, skipping")
        return {"video": video_path.name, "frames": 0, "missed": 0,
                "opened": False, "rendered": None}

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    mode = values["RENDER_MODE"]
    suffix = st.render_suffix(values)
    writer = None
    codec = None
    out_path = None
    if mode != "none":
        out_path = video_path.parent / f"{video_path.stem}{suffix}.mp4"
        if out_path.exists():
            log(f"    {out_path.name} exists, not overwriting; "
                f"landmarks will still be extracted")
            out_path = None
        else:
            writer, codec = _open_writer(
                out_path, fps, width, height, values["VIDEO_CODEC"], log)
            if writer is None:
                log(f"    WARNING no working codec for {out_path.name}; "
                    f"continuing without rendered video")
                out_path = None

    n_landmarks = mesh.n_landmarks
    folder = str(video_path.parent)
    name = video_path.name
    principal_only = values["PRINCIPAL_FACE_ONLY"]
    write_empty = values["WRITE_EMPTY_ROWS"]
    every = values["PROGRESS_EVERY"]

    frame_idx = 0
    missed = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        faces = mesh.process(rgb, frame_idx)

        canvas = frame
        if mode == "mask":
            canvas = np.zeros((height, width, 3), dtype=np.uint8)

        if faces:
            if principal_only:
                faces = [_pick_principal_face(faces)]
            for face_id, face in enumerate(faces):
                if writer is not None:
                    mesh.draw(canvas, face)
                for lm_idx, (x, y, z) in enumerate(face):
                    sink([
                        folder, name, frame_idx, face_id, lm_idx,
                        round(x, 6), round(y, 6), round(z, 6),
                        round(x * width, 2), round(y * height, 2),
                    ])
        else:
            missed += 1
            if write_empty:
                for lm_idx in range(n_landmarks):
                    sink([folder, name, frame_idx, "", lm_idx,
                          "", "", "", "", ""])

        if writer is not None:
            writer.write(canvas)
        frame_idx += 1

        if every and frame_idx % every == 0:
            pct = (frame_idx / total * 100) if total else 0
            log(f"      frame {frame_idx}/{total or '?'} ({pct:.0f}%)")

    cap.release()
    if writer is not None:
        writer.release()

    tail = f" -> {out_path.name} [{codec}]" if out_path else ""
    log(f"    {frame_idx} frames{tail}")
    if missed:
        log(f"      {missed} frames with no face detected"
            + (", blank rows written" if write_empty else ", no rows written"))

    return {"video": name, "frames": frame_idx, "missed": missed,
            "opened": True, "rendered": str(out_path) if out_path else None}


# ======================================================================
#  Whole run
# ======================================================================

def run(values: dict | None = None,
        folders: list[str] | None = None,
        out_csv: str | None = None,
        log: Callable[[str], None] = _noop,
        should_stop: Callable[[], bool] = lambda: False) -> dict:
    """Extract landmarks from every configured folder into one CSV."""
    cv2, np = _import_backends()
    from .mesh import choose, open_mesh

    values = values or st.load()

    raw_folders = folders if folders is not None else values["VIDEO_FOLDERS"]
    valid = []
    for item in raw_folders:
        path = Path(item).expanduser()
        if path.is_dir():
            valid.append(path)
        else:
            log(f"WARNING {item} is not a folder, skipping")
    if not valid:
        raise RuntimeError(
            "No valid input folders. Set Video folders under Paths, or pass "
            "a folder on the command line.")

    target = out_csv or values["LANDMARKS_CSV"]
    if not target:
        raise RuntimeError(
            "No output path. Set Landmarks csv under Paths, or pass -o.")
    target_path = st.resolve_output_path(target, "LANDMARKS_CSV")
    if target_path.name != Path(str(target)).name:
        log(f"Landmarks csv named a folder, writing to {target_path}")

    backend = choose(values.get("MESH_BACKEND", "auto"))
    log(f"Using the MediaPipe {backend} face-mesh API")
    if backend == "tasks" and not values["REFINE_LANDMARKS"]:
        log("  note: the tasks backend always returns 478 landmarks, so "
            "Refine landmarks has no effect on this run")

    started = time.time()
    totals = {"folders": 0, "videos": 0, "frames": 0, "rows": 0, "missed": 0}

    with open(target_path, "w", newline="", encoding="utf-8") as global_file:
        global_writer = csv.writer(global_file)
        global_writer.writerow(CSV_HEADER)

        with open_mesh(values, log=log) as mesh:

            for f_idx, folder in enumerate(valid, 1):
                if should_stop():
                    log("Stopped.")
                    break

                videos = find_videos(folder, values)
                log("")
                log(f"[folder {f_idx}/{len(valid)}] {folder}")
                if not videos:
                    log("  no videos found, skipping")
                    continue
                log(f"  {len(videos)} video(s)")
                totals["folders"] += 1

                folder_file = None
                folder_writer = None
                if values["WRITE_PER_FOLDER_CSV"]:
                    folder_csv = folder / "face_landmarks.csv"
                    folder_file = open(folder_csv, "w", newline="",
                                       encoding="utf-8")
                    folder_writer = csv.writer(folder_file)
                    folder_writer.writerow(CSV_HEADER)

                folder_rows = 0

                def sink(row, _gw=global_writer, _fw=folder_writer):
                    nonlocal folder_rows
                    _gw.writerow(row)
                    if _fw is not None:
                        _fw.writerow(row)
                    folder_rows += 1

                try:
                    for v_idx, video in enumerate(videos, 1):
                        if should_stop():
                            log("Stopped.")
                            break
                        log(f"  [{v_idx}/{len(videos)}] {video.name}")
                        summary = process_video(
                            video, mesh, sink, values, log)
                        totals["videos"] += 1
                        totals["frames"] += summary["frames"]
                        totals["missed"] += summary["missed"]
                finally:
                    if folder_file is not None:
                        folder_file.close()
                        log(f"  {folder_rows:,} rows -> "
                            f"{folder / 'face_landmarks.csv'}")

                totals["rows"] += folder_rows

    elapsed = time.time() - started
    n_lm = 478 if backend == "tasks" else st.num_landmarks(values)
    exact = totals["rows"] % n_lm == 0

    log("")
    log(f"  done in {elapsed:.1f}s")
    log(f"  folders   {totals['folders']}")
    log(f"  videos    {totals['videos']}")
    log(f"  frames    {totals['frames']:,}")
    log(f"  rows      {totals['rows']:,}")
    log(f"  rows / {n_lm} is exact: {'yes' if exact else 'NO'}")
    if not exact:
        log("  a non-exact row count means some frames wrote a partial set "
            "of landmarks; check the warnings above")
    log(f"  written   {target_path}")

    return {**totals, "elapsed_s": round(elapsed, 1), "backend": backend,
            "csv": str(target_path), "rows_exact": exact}
