"""
Every tunable value in facekit, with its type, default and help text.

This module is the single source of truth. The web interface is generated
from `SETTINGS`, the command line derives `--set NAME=VALUE` from it, and
each stage reads values through `load()`. Exposing a new option means
adding one `S(...)` entry below; there is no field list in the HTML and no
argument list in the CLI to keep in step.

Values are saved to ~/.facekit/settings.json and reloaded next time.
Named presets live in ~/.facekit/presets/<name>.json.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(os.environ.get("FACEKIT_HOME", Path.home() / ".facekit"))
SETTINGS_FILE = CONFIG_DIR / "settings.json"
PRESETS_DIR = CONFIG_DIR / "presets"

STAGES = ("landmarks", "mouth", "events", "metrics")

STAGE_TITLES = {
    "landmarks": "Landmark extraction",
    "mouth": "Mouth-area series",
    "events": "Peak and valley review",
    "metrics": "Metrics and plots",
}


@dataclass(frozen=True)
class S:
    """One setting."""

    name: str
    default: Any
    group: str
    # bool | int | float | str | choice | multichoice | path | intlist | strlist
    type: str = "str"
    help: str = ""
    choices: tuple = ()
    advanced: bool = False
    stages: tuple = STAGES
    min: float | None = None
    max: float | None = None
    unit: str = ""

    @property
    def label(self) -> str:
        return self.name.replace("_", " ").capitalize()


# ======================================================================
#  Paths
# ======================================================================

_PATHS = [
    S("VIDEO_FOLDERS", [], "Paths", stages=("landmarks",), type="strlist",
      help="Folders holding the recordings to analyse. Each is scanned "
           "recursively. Videos facekit itself produced are skipped."),
    S("LANDMARKS_CSV", "", "Paths", stages=("landmarks", "mouth"), type="path",
      help="Where stage 1 writes the combined landmark table, and where "
           "stage 2 reads it from."),
    S("MOUTH_CSV", "", "Paths", stages=("mouth", "events"), type="path",
      help="Where stage 2 writes the downsampled table with normalised "
           "mouth area, and where the picker reads it from."),
    S("EVENTS_CSV", "", "Paths", stages=("events", "metrics"), type="path",
      help="Where the picker writes reviewed peaks and valleys, and where "
           "stage 4 reads them from."),
    S("PLOTS_DIR", "", "Paths", stages=("metrics",), type="path",
      help="Output folder for the metrics CSV and the plot tree."),
    S("WRITE_PER_FOLDER_CSV", True, "Paths", stages=("landmarks",), type="bool",
      advanced=True,
      help="Also write a face_landmarks.csv inside each input folder, "
           "alongside the combined table. Useful when folders are processed "
           "on different days."),
]

# ======================================================================
#  Stage 1 - landmark extraction
# ======================================================================

_MESH = [
    S("MESH_BACKEND", "auto", "Face mesh", stages=("landmarks",), type="choice",
      choices=("auto", "solutions", "tasks"),
      help="Which MediaPipe face-mesh API to use. MediaPipe has two and "
           "which you get depends on the version installed: solutions is the "
           "original, needs no model download and honours Refine landmarks; "
           "tasks is the current one, always returns 478 points and fetches a "
           "3 MB model once. auto prefers solutions where it exists, because "
           "it reproduces earlier output exactly. Check the environment panel "
           "to see which you have."),
    S("REFINE_LANDMARKS", True, "Face mesh", stages=("landmarks",), type="bool",
      help="Include the iris landmarks: 478 points instead of 468. Required "
           "for pupil-based normalisation, so leave this on unless you have "
           "a reason not to."),
    S("MAX_FACES", 1, "Face mesh", stages=("landmarks",), type="int", min=1, max=5,
      help="Maximum faces detected per frame. With more than one, only the "
           "principal face is kept if that option is on."),
    S("PRINCIPAL_FACE_ONLY", True, "Face mesh", stages=("landmarks",), type="bool",
      help="When several faces are found, keep only the one with the largest "
           "bounding box and record it as face_id 0. Turn this off to keep "
           "every face, each under its own face_id."),
    S("MIN_DETECTION_CONFIDENCE", 0.7, "Face mesh", stages=("landmarks",),
      type="float", min=0.0, max=1.0,
      help="Confidence below which a detection is discarded."),
    S("MIN_TRACKING_CONFIDENCE", 0.7, "Face mesh", stages=("landmarks",),
      type="float", min=0.0, max=1.0,
      help="Confidence below which tracking is dropped and detection reruns."),
    S("VIDEO_EXTENSIONS", [".mp4", ".mov", ".avi", ".mkv"], "Face mesh",
      stages=("landmarks",), type="strlist", advanced=True,
      help="Extensions treated as video. Matching is case-insensitive."),
]

_RENDER = [
    S("RENDER_MODE", "overlay", "Rendered video", stages=("landmarks",), type="choice",
      choices=("overlay", "mask", "none"),
      help="overlay draws the mesh on the recording, which is what you want "
           "for checking tracking against the face. mask draws the mesh on "
           "black, which is shareable without showing the participant. none "
           "skips video writing and is roughly twice as fast."),
    S("OUTPUT_SUFFIX", "", "Rendered video", stages=("landmarks",), type="str",
      help="Suffix for the rendered file. Left empty it follows the render "
           "mode: _landmarks for overlay, _only_mask for mask. Never reuse a "
           "suffix that already exists in the folder; facekit will not "
           "overwrite, it will skip."),
    S("VIDEO_CODEC", "avc1", "Rendered video", stages=("landmarks",), type="choice",
      choices=("avc1", "mp4v"),
      help="avc1 is H.264 and plays in a browser, which the review step "
           "needs. mp4v is more widely available in minimal OpenCV builds "
           "but will not play in Chrome. facekit falls back to mp4v with a "
           "warning if avc1 is missing."),
    S("PROGRESS_EVERY", 100, "Rendered video", stages=("landmarks",), type="int",
      min=1, advanced=True,
      help="Report progress every N frames."),
    S("WRITE_EMPTY_ROWS", True, "Rendered video", stages=("landmarks",), type="bool",
      advanced=True,
      help="When no face is found, still write one blank row per landmark so "
           "the row count stays an exact multiple of the landmark count. "
           "This is what makes a dropout visible rather than silent."),
]

# ======================================================================
#  Stage 2 - mouth area
# ======================================================================

_MOUTH = [
    S("KEEP_EVERY_N", 5, "Mouth area", stages=("mouth",), type="int", min=1, max=60,
      help="Keep one representative frame in N. The mouth area is still "
           "computed on every frame; the representative frame carries the "
           "average over its own window, so downsampling averages rather "
           "than discards."),
    S("INNER_LIP_IDS", [78, 191, 80, 81, 82, 13, 312, 311, 310, 415,
                        308, 324, 318, 402, 317, 14, 87, 178, 88, 95],
      "Mouth area", stages=("mouth", "metrics"), type="intlist", advanced=True,
      help="The inner-lip contour as an ordered loop. Order matters: the "
           "area is a shoelace sum over the polygon, so a shuffled list "
           "gives a self-intersecting outline and a meaningless area."),
    S("LEFT_PUPIL_ID", 473, "Mouth area", stages=("mouth", "metrics"), type="int",
      advanced=True,
      help="Iris centre landmark for one eye. Which of the two is 'left' "
           "does not affect any measure: distance is symmetric, and roll "
           "correction orders the pair by image position rather than by "
           "this label."),
    S("RIGHT_PUPIL_ID", 468, "Mouth area", stages=("mouth", "metrics"), type="int",
      advanced=True,
      help="Iris centre landmark for the other eye."),
    S("NORMALISE_BY", "ipd_squared", "Mouth area", stages=("mouth",), type="choice",
      choices=("ipd_squared", "none"),
      help="Dividing area by the squared inter-pupil distance makes it "
           "independent of how far the participant sat from the camera. "
           "Choose none only if every recording has a fixed, known geometry."),
    S("KEEP_LANDMARKS", "needed", "Mouth area", stages=("mouth",), type="choice",
      choices=("needed", "all"),
      help="Which landmarks to carry into the downsampled table. needed "
           "keeps the inner lip contour, the two irises and the four "
           "mid-arc points, which is every landmark the later stages read, "
           "and makes the file roughly fifteen times smaller. Choose all if "
           "you expect to measure something else later without rerunning "
           "stage 1."),
]

# ======================================================================
#  Stage 3 - peak and valley review
# ======================================================================

_EVENTS = [
    S("SMOOTHING_WINDOW", 0, "Detection", stages=("events",), type="int", min=0, max=31,
      help="Moving-average width used for detection, in representative "
           "frames. 0 derives it from the length of each recording, which is "
           "the right default when recordings differ in length."),
    S("SENSITIVITY", 50, "Detection", stages=("events",), type="int", min=5, max=95,
      unit="%",
      help="How readily a bump counts as a peak. Higher finds more events "
           "and more noise. This is the slider you will actually use during "
           "review; the saved value is only the starting position."),
    S("MIN_PEAK_DISTANCE_FRAC", 0.03, "Detection", stages=("events",), type="float",
      min=0.001, max=0.5, advanced=True,
      help="Minimum spacing between events as a fraction of recording "
           "length. Keeps one broad opening from being read as several."),
    S("MIN_PROMINENCE_FRAC", 0.02, "Detection", stages=("events",), type="float",
      min=0.0, max=1.0, advanced=True,
      help="Floor on peak prominence as a fraction of the signal range, "
           "applied on top of the sensitivity-derived threshold."),
    S("ENFORCE_ALTERNATING", True, "Detection", stages=("events",), type="bool",
      help="Require peaks and valleys to alternate, keeping the most extreme "
           "of any run of same-type events. Without this a noisy plateau "
           "produces several peaks with no valley between them, and no rise "
           "can be measured across it."),
]

_SLOPE = [
    S("SLOPE_METHOD", "spline", "Rise slope", stages=("events",), type="choice",
      choices=("spline", "diagonal"),
      help="spline fits a smoothing cubic spline to the whole recording and "
           "takes the steepest point of its derivative on each rise. "
           "diagonal is the straight valley-to-peak line, which "
           "underestimates whenever the rise is not linear. Both are written "
           "to the CSV either way; this only sets which one the plots use."),
    S("SPLINE_SMOOTHING", 0.0, "Rise slope", stages=("events",), type="float", min=0.0,
      help="Spline smoothing factor s. 0 estimates it from the "
           "high-frequency content of each recording. Set it by hand only to "
           "reproduce an earlier run exactly."),
    S("SLOPE_SAMPLES", 200, "Rise slope", stages=("events",), type="int", min=10,
      max=5000, advanced=True,
      help="Points at which the spline derivative is sampled across a rise "
           "when locating its maximum."),
    S("GAUSSIAN_SIGMA_FRAC", 0.012, "Rise slope", stages=("events",), type="float",
      min=0.001, max=0.2, advanced=True,
      help="Width of the Gaussian smoother the browser uses to preview the "
           "fitted curve, as a fraction of recording length. The exported "
           "numbers come from the spline, not from this; it exists so the "
           "curve you see while reviewing is close to the curve used."),
]

# ======================================================================
#  Stage 4 - metrics and plots
# ======================================================================

_GEOMETRY = [
    S("HEAD_ROLL_CORRECTION", True, "Geometry", stages=("metrics",), type="bool",
      help="Rotate each frame so the eye line is horizontal before measuring. "
           "Without it, a tilted head inflates vertical opening and shifts "
           "the midline, so left-right asymmetry partly reflects posture."),
    S("MIRROR_MODE", "auto", "Geometry", stages=("metrics",), type="choice",
      choices=("auto", "unmirrored", "mirrored"),
      help="Whether the participant's left appears on the left of the frame, "
           "as a webcam preview usually shows it. This only affects the two "
           "half-area measures, which are geometric; every other left and "
           "right comes from the landmark itself and is already correct "
           "either way. auto reads the side from where the labelled mouth "
           "corners actually fall, and is right unless the face is turned "
           "far enough that both corners sit on one side of the midline."),
    S("PATIENT_RIGHT_CORNER_ID", 78, "Geometry", stages=("metrics",), type="int",
      advanced=True,
      help="Mouth corner on the participant's right in an unmirrored frame."),
    S("PATIENT_LEFT_CORNER_ID", 308, "Geometry", stages=("metrics",), type="int",
      advanced=True,
      help="Mouth corner on the participant's left in an unmirrored frame."),
    S("RIGHT_UPPER_MID_ID", 81, "Geometry", stages=("metrics",), type="int",
      advanced=True,
      help="Upper-lip mid-arc landmark on the participant's right, used for "
           "vertical opening."),
    S("RIGHT_LOWER_MID_ID", 178, "Geometry", stages=("metrics",), type="int",
      advanced=True, help="Lower-lip mid-arc landmark, participant's right."),
    S("LEFT_UPPER_MID_ID", 311, "Geometry", stages=("metrics",), type="int",
      advanced=True, help="Upper-lip mid-arc landmark, participant's left."),
    S("LEFT_LOWER_MID_ID", 402, "Geometry", stages=("metrics",), type="int",
      advanced=True, help="Lower-lip mid-arc landmark, participant's left."),
]

_GROUPING = [
    S("TASK_FROM_FILENAME", True, "Grouping", stages=("metrics",), type="bool",
      help="Read the task from the part of the filename before the first "
           "underscore, and the stimulus condition from the part after it. "
           "With this off, every recording in a folder is treated as one task."),
    S("COLLAPSE_TRIAL_NUMBERS", True, "Grouping", stages=("metrics",), type="bool",
      help="Treat smile1, smile2 and smile_3 as repetitions of smile rather "
           "than three separate tasks."),
    S("DATE_FROM_FOLDER", True, "Grouping", stages=("metrics",), type="bool",
      help="Use the name of the folder holding each recording as the session "
           "date. This is what orders the x axis, so folders named 0309, "
           "0311 and so on sort correctly as numbers."),
]

_PLOTS = [
    S("CENTRAL_TENDENCIES", ["median", "robust_mean"], "Plots", stages=("metrics",),
      type="multichoice", choices=("median", "robust_mean", "mean"),
      help="Which summary bar to draw on each group. Every one you pick gets "
           "its own output folder containing the same plots, so you can "
           "compare summaries without rerunning. robust_mean averages the "
           "interquartile range only, dropping the top and bottom quarter."),
    S("BEST_QUARTILE", True, "Plots", stages=("metrics",), type="bool",
      help="Also produce every plot using only each group's best quartile. "
           "For maximum-effort tasks this is closer to what the participant "
           "can do than an average over attempts of varying effort."),
    S("BEST_QUARTILE_PERCENTILE", 75.0, "Plots", stages=("metrics",), type="float",
      min=0.0, max=99.0, advanced=True,
      help="Percentile above which values are kept for the best-quartile "
           "plots."),
    S("PLOT_FORMATS", ["pdf", "png"], "Plots", stages=("metrics",), type="multichoice",
      choices=("pdf", "png", "svg"),
      help="Formats each plot is saved in. PDF stays sharp in a manuscript; "
           "PNG is for looking at quickly."),
    S("PLOT_DPI", 72, "Plots", stages=("metrics",), type="int", min=36, max=600,
      advanced=True, help="Resolution for raster formats."),
    S("STRIP_JITTER", 0.1, "Plots", stages=("metrics",), type="float", min=0.0, max=0.4,
      advanced=True,
      help="Horizontal spread of points within a group, so overlapping "
           "values stay countable."),
    S("JITTER_SEED", 42, "Plots", stages=("metrics",), type="int", advanced=True,
      help="Seed for that spread, so a regenerated figure is identical."),
    S("SKIP_PLOTS", False, "Plots", stages=("metrics",), type="bool",
      help="Compute and save the metrics CSV, then stop. Useful when you "
           "only want the numbers, or when checking a run before committing "
           "to a few hundred figures."),
]

# ======================================================================
#  Server
# ======================================================================

_SERVER = [
    S("HOST", "127.0.0.1", "Server", stages=STAGES, type="str", advanced=True,
      help="Interface bind address. Leave as the loopback address: the "
           "interface has no authentication and its file browser can see the "
           "whole filesystem."),
    S("PORT", 7332, "Server", stages=STAGES, type="int", min=1024, max=65535,
      help="Interface port. 7332 rather than speechkit's 7331, so both tools "
           "can run at once."),
    S("OPEN_BROWSER", True, "Server", stages=STAGES, type="bool",
      help="Open a browser window on start."),
]

SETTINGS: tuple[S, ...] = tuple(
    _PATHS + _MESH + _RENDER + _MOUTH + _EVENTS + _SLOPE
    + _GEOMETRY + _GROUPING + _PLOTS + _SERVER
)

BY_NAME = {s.name: s for s in SETTINGS}
DEFAULTS = {s.name: s.default for s in SETTINGS}

GROUP_ORDER = []
for _s in SETTINGS:
    if _s.group not in GROUP_ORDER:
        GROUP_ORDER.append(_s.group)


# ======================================================================
#  Coercion
# ======================================================================

class SettingError(ValueError):
    """A value that cannot be used for the setting it was given for."""


_TRUE = {"1", "true", "yes", "on", "y", "t"}
_FALSE = {"0", "false", "no", "off", "n", "f"}


def coerce(name: str, value: Any) -> Any:
    """Turn a value of unknown provenance into the right type for `name`.

    Accepts what a form field, a JSON file or a --set flag would give,
    and raises SettingError with a readable message otherwise.
    """
    if name not in BY_NAME:
        raise SettingError(f"Unknown setting: {name}")
    spec = BY_NAME[name]
    t = spec.type

    try:
        if t == "bool":
            if isinstance(value, bool):
                out = value
            else:
                text = str(value).strip().lower()
                if text in _TRUE:
                    out = True
                elif text in _FALSE:
                    out = False
                else:
                    raise SettingError(
                        f"{name} takes true or false, not {value!r}")
        elif t == "int":
            out = int(str(value).strip())
        elif t == "float":
            out = float(str(value).strip())
        elif t in ("str", "path"):
            out = str(value).strip()
        elif t == "choice":
            out = str(value).strip()
            if out not in spec.choices:
                raise SettingError(
                    f"{name} must be one of {', '.join(spec.choices)}, "
                    f"not {out!r}")
        elif t == "multichoice":
            items = _as_list(value)
            out = [str(i).strip() for i in items if str(i).strip()]
            bad = [i for i in out if i not in spec.choices]
            if bad:
                raise SettingError(
                    f"{name}: {', '.join(bad)} not among "
                    f"{', '.join(spec.choices)}")
        elif t == "intlist":
            out = [int(str(i).strip()) for i in _as_list(value) if str(i).strip()]
        elif t == "strlist":
            out = [str(i).strip() for i in _as_list(value) if str(i).strip()]
        else:
            raise SettingError(f"{name} has unknown type {t!r}")
    except SettingError:
        raise
    except (TypeError, ValueError) as exc:
        raise SettingError(f"{name}: {exc}") from exc

    if spec.min is not None and isinstance(out, (int, float)) and out < spec.min:
        raise SettingError(f"{name} must be at least {spec.min}, got {out}")
    if spec.max is not None and isinstance(out, (int, float)) and out > spec.max:
        raise SettingError(f"{name} must be at most {spec.max}, got {out}")
    return out


def _as_list(value: Any) -> list:
    if isinstance(value, (list, tuple)):
        return list(value)
    text = str(value).strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
    return [p for p in re.split(r"[,\n;]+", text) if p.strip()]


# ======================================================================
#  Load and save
# ======================================================================

def load(overrides: dict | None = None) -> dict:
    """Defaults, then the saved file, then `overrides`.

    Unknown or unusable saved keys are dropped rather than raising, so a
    settings file from an older version still starts the interface.
    """
    values = dict(DEFAULTS)
    if SETTINGS_FILE.exists():
        try:
            saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            saved = {}
        for key, raw in (saved or {}).items():
            if key not in BY_NAME:
                continue
            try:
                values[key] = coerce(key, raw)
            except SettingError:
                continue
    for key, raw in (overrides or {}).items():
        values[key] = coerce(key, raw)
    return values


def save(values: dict) -> Path:
    """Write the settings that differ from the defaults."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    trimmed = {
        k: v for k, v in values.items()
        if k in BY_NAME and v != DEFAULTS[k]
    }
    SETTINGS_FILE.write_text(
        json.dumps(trimmed, indent=2, sort_keys=True), encoding="utf-8")
    return SETTINGS_FILE


def parse_set_flags(pairs: list[str]) -> dict:
    """Turn ['PORT=8080', 'SENSITIVITY=60'] into a coerced dict."""
    out = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise SettingError(
                f"--set expects NAME=VALUE, got {pair!r}")
        name, _, raw = pair.partition("=")
        name = name.strip().upper()
        out[name] = coerce(name, raw)
    return out


def for_stage(stage: str, include_advanced: bool = True) -> list[S]:
    """Settings a stage reads, in registry order."""
    return [
        s for s in SETTINGS
        if stage in s.stages and (include_advanced or not s.advanced)
    ]


# ======================================================================
#  Presets
# ======================================================================

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def preset_path(name: str) -> Path:
    safe = _SAFE_NAME.sub("_", name.strip()) or "unnamed"
    return PRESETS_DIR / f"{safe}.json"


def list_presets() -> list[str]:
    if not PRESETS_DIR.is_dir():
        return []
    return sorted(p.stem for p in PRESETS_DIR.glob("*.json"))


def save_preset(name: str, values: dict) -> Path:
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    path = preset_path(name)
    trimmed = {k: v for k, v in values.items() if k in BY_NAME}
    path.write_text(json.dumps(trimmed, indent=2, sort_keys=True),
                    encoding="utf-8")
    return path


def load_preset(name: str) -> dict:
    path = preset_path(name)
    if not path.exists():
        raise SettingError(f"No preset named {name!r} in {PRESETS_DIR}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {k: coerce(k, v) for k, v in raw.items() if k in BY_NAME}


def delete_preset(name: str) -> bool:
    path = preset_path(name)
    if path.exists():
        path.unlink()
        return True
    return False


# ======================================================================
#  Derived helpers the stages share
# ======================================================================

def num_landmarks(values: dict) -> int:
    return 478 if values["REFINE_LANDMARKS"] else 468


def render_suffix(values: dict) -> str:
    """The suffix for rendered video, resolving the empty default."""
    explicit = (values.get("OUTPUT_SUFFIX") or "").strip()
    if explicit:
        return explicit
    return {"overlay": "_landmarks", "mask": "_only_mask", "none": ""}[
        values["RENDER_MODE"]]


# Each file-valued path setting has a natural filename, used when the
# interface hands back a folder instead of a file.
DEFAULT_FILENAMES = {
    "LANDMARKS_CSV": "all_landmarks.csv",
    "MOUTH_CSV": "mouth_area.csv",
    "EVENTS_CSV": "reviewed_events.csv",
}


def resolve_output_path(raw: str, setting: str) -> Path:
    """A writable file path, from a value that may name a folder.

    Windows raises PermissionError rather than IsADirectoryError when a
    directory is opened for writing, so a folder left in a file field
    surfaces as a bare permission error with no hint of the cause. It is
    caught here instead, and a folder is completed with the setting's
    natural filename rather than rejected.
    """
    path = Path(str(raw).strip()).expanduser()
    if path.is_dir():
        name = DEFAULT_FILENAMES.get(setting)
        if name is None:
            raise SettingError(
                f"{setting} is set to a folder ({path}) but needs a file.")
        path = path / name
    if not path.name:
        raise SettingError(f"{setting} has no filename in it: {raw!r}")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SettingError(
            f"Cannot create the folder for {setting}: {path.parent} ({exc})"
        ) from exc
    return path


def resolve_input_path(raw: str, setting: str) -> Path:
    """An existing, readable file path, with directories rejected clearly."""
    path = Path(str(raw).strip()).expanduser()
    if path.is_dir():
        candidates = sorted(path.glob("*.csv"))
        listing = ""
        if candidates:
            shown = ", ".join(c.name for c in candidates[:6])
            more = " and others" if len(candidates) > 6 else ""
            listing = (f" The CSV files in it are: {shown}{more}. "
                       f"Add the one you want to the end of the path.")
        raise SettingError(
            f"{setting} is set to a folder ({path}) but needs a file.{listing}")
    if not path.exists():
        raise SettingError(f"{setting}: no such file: {path}")
    if not path.is_file():
        raise SettingError(f"{setting} is not a readable file: {path}")
    return path


def all_generated_suffixes() -> tuple[str, ...]:
    """Suffixes facekit may have written, so inputs can exclude them."""
    return ("_landmarks", "_only_mask", "_mesh")
