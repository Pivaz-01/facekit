"""
Stage 4: the measures, and the figures.

Every frame is first rotated so the eye line is horizontal, then all
distances are divided by the inter-pupil distance and all areas by its
square. What is left is a set of numbers comparable across sessions,
cameras and participants.

Left and right are reported separately throughout, because the question
these recordings are usually asked is whether the two sides of the face
move alike.

Plots are strip plots: one point per measurement, a bar for the group
summary. Bars hide how many attempts there were and how spread out they
are, and with ten or twenty repetitions per session that is most of what
you want to see.
"""

from __future__ import annotations

import gc
import re
import time
from collections import OrderedDict
from pathlib import Path
from typing import Callable

from . import settings as st

CHUNK_ROWS = 300_000


def _noop(msg: str) -> None:
    pass


def requirements() -> dict:
    missing = []
    for module, package in (("pandas", "pandas"), ("numpy", "numpy"),
                            ("matplotlib", "matplotlib")):
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    return {
        "stage": "metrics",
        "ok": not missing,
        "missing": missing,
        "install": f"pip install {' '.join(missing)}" if missing else "",
    }


# ======================================================================
#  Central tendency
# ======================================================================

def central_median(vals):
    import numpy as np

    arr = np.asarray(vals, dtype=float)
    arr = arr[np.isfinite(arr)]
    return float(np.median(arr)) if len(arr) else float("nan")


def central_mean(vals):
    import numpy as np

    arr = np.asarray(vals, dtype=float)
    arr = arr[np.isfinite(arr)]
    return float(np.mean(arr)) if len(arr) else float("nan")


def central_robust_mean(vals):
    """Mean of the interquartile range: the top and bottom quarter dropped.

    With fewer than four values there is nothing to trim, so this falls
    back to the ordinary mean rather than returning something that looks
    trimmed but is not.
    """
    import numpy as np

    arr = np.asarray(vals, dtype=float)
    arr = arr[np.isfinite(arr)]
    if not len(arr):
        return float("nan")
    if len(arr) < 4:
        return float(np.mean(arr))
    q25, q75 = np.percentile(arr, [25, 75])
    kept = arr[(arr >= q25) & (arr <= q75)]
    return float(np.mean(kept if len(kept) else arr))


CENTRAL_FUNCTIONS = {
    "median": central_median,
    "robust_mean": central_robust_mean,
    "mean": central_mean,
}


# ======================================================================
#  Naming
# ======================================================================

_TRIAL_TAIL = re.compile(r"[_-]?\d+$")
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def parse_video_info(folder: str, video: str, values: dict) -> tuple:
    """Session date, task and stimulus from the folder and file name."""
    date = Path(str(folder)).name if values["DATE_FROM_FOLDER"] else ""
    stem = Path(str(video)).stem

    if not values["TASK_FROM_FILENAME"]:
        return date, "all", ""

    task, _, rest = stem.partition("_")
    stim = rest.upper() if rest else ""
    if values["COLLAPSE_TRIAL_NUMBERS"]:
        task = _TRIAL_TAIL.sub("", task) or task
    return date, task or "unknown", stim


def safe_name(text) -> str:
    return _UNSAFE.sub("_", str(text)) or "unnamed"


def group_sort_key(label: str):
    """Order groups by session date, numerically where the name allows it."""
    date_part, _, stim_part = str(label).partition("\n")
    try:
        return (0, int(date_part), stim_part)
    except ValueError:
        return (1, 0, f"{date_part}{stim_part}")


# ======================================================================
#  Geometry
# ======================================================================

def shoelace_area(xs, ys) -> float:
    total = 0.0
    n = len(xs)
    for i in range(n):
        j = (i + 1) % n
        total += xs[i] * ys[j] - xs[j] * ys[i]
    return abs(total) / 2.0


def polygon_angle_at(xs, ys, idx: int) -> float:
    """Interior angle of the contour at one vertex, in degrees."""
    import numpy as np

    n = len(xs)
    prev_i, next_i = (idx - 1) % n, (idx + 1) % n
    ax, ay = xs[prev_i] - xs[idx], ys[prev_i] - ys[idx]
    bx, by = xs[next_i] - xs[idx], ys[next_i] - ys[idx]
    dot = ax * bx + ay * by
    cross = ax * by - ay * bx
    return float(np.degrees(np.arctan2(abs(cross), dot)))


def clip_polygon_half(xs, ys, midline_x: float, keep_lower: bool):
    """Sutherland-Hodgman clip of the contour to one side of the midline."""
    def inside(x):
        return x <= midline_x if keep_lower else x >= midline_x

    out_xs, out_ys = [], []
    n = len(xs)
    for i in range(n):
        j = (i + 1) % n
        xi, yi = xs[i], ys[i]
        xj, yj = xs[j], ys[j]
        i_in, j_in = inside(xi), inside(xj)
        if i_in and j_in:
            out_xs.append(xj)
            out_ys.append(yj)
        elif i_in and not j_in:
            t = (midline_x - xi) / (xj - xi) if xj != xi else 0.0
            out_xs.append(midline_x)
            out_ys.append(yi + t * (yj - yi))
        elif not i_in and j_in:
            t = (midline_x - xi) / (xj - xi) if xj != xi else 0.0
            out_xs.append(midline_x)
            out_ys.append(yi + t * (yj - yi))
            out_xs.append(xj)
            out_ys.append(yj)
    return out_xs, out_ys


def correct_head_roll(points: dict, values: dict):
    """Rotate so the eye line is horizontal, about the mid-pupil point.

    The two irises are ordered by image position rather than by which
    landmark id was called left, so the rotation is the same whichever
    way round those ids are and cannot come out flipped by half a turn.
    """
    import numpy as np

    left = points.get(values["LEFT_PUPIL_ID"])
    right = points.get(values["RIGHT_PUPIL_ID"])
    if left is None or right is None:
        return points, float("nan"), None, float("nan")

    lower, upper = sorted((left, right), key=lambda p: p[0])
    dx = upper[0] - lower[0]
    dy = upper[1] - lower[1]
    ipd = float(np.hypot(dx, dy))
    mid = ((left[0] + right[0]) / 2.0, (left[1] + right[1]) / 2.0)
    if ipd == 0 or not np.isfinite(ipd):
        return points, float("nan"), None, float("nan")

    theta = float(np.arctan2(dy, dx))
    if not values["HEAD_ROLL_CORRECTION"]:
        return points, ipd, mid, np.degrees(theta)

    cos_t, sin_t = np.cos(-theta), np.sin(-theta)
    rotated = {}
    for key, (x, y) in points.items():
        cx, cy = x - mid[0], y - mid[1]
        rotated[key] = (cx * cos_t - cy * sin_t + mid[0],
                        cx * sin_t + cy * cos_t + mid[1])
    return rotated, ipd, mid, float(np.degrees(theta))


def _patient_left_is_higher_x(xs, lip_ids, values) -> bool:
    """Which side of the frame the participant's left sits on."""
    mode = values["MIRROR_MODE"]
    if mode == "unmirrored":
        return True
    if mode == "mirrored":
        return False
    left_i = lip_ids.index(values["PATIENT_LEFT_CORNER_ID"])
    right_i = lip_ids.index(values["PATIENT_RIGHT_CORNER_ID"])
    return xs[left_i] >= xs[right_i]


def compute_metrics(points: dict, values: dict) -> tuple[dict, float, float]:
    """Every per-frame measure, or an empty dict if the frame is unusable."""
    import numpy as np

    lip_ids = list(values["INNER_LIP_IDS"])
    corrected, ipd, mid, roll = correct_head_roll(points, values)
    if mid is None or not np.isfinite(ipd) or ipd == 0:
        return {}, float("nan"), float("nan")

    midline_x, midline_y = mid
    xs, ys = [], []
    for lm_id in lip_ids:
        point = corrected.get(lm_id)
        if point is None:
            return {}, ipd, roll
        xs.append(point[0])
        ys.append(point[1])

    left_i = lip_ids.index(values["PATIENT_LEFT_CORNER_ID"])
    right_i = lip_ids.index(values["PATIENT_RIGHT_CORNER_ID"])
    lx, ly = xs[left_i], ys[left_i]
    rx, ry = xs[right_i], ys[right_i]

    ipd2 = ipd ** 2
    out = {
        "inner_mouth_area": shoelace_area(xs, ys) / ipd2,
        "left_corner_to_midline": abs(lx - midline_x) / ipd,
        "right_corner_to_midline": abs(rx - midline_x) / ipd,
        "left_corner_angle": polygon_angle_at(xs, ys, left_i),
        "right_corner_angle": polygon_angle_at(xs, ys, right_i),
        "left_corner_disp": float(np.hypot(lx - midline_x, ly - midline_y)) / ipd,
        "right_corner_disp": float(np.hypot(rx - midline_x, ry - midline_y)) / ipd,
    }

    left_higher = _patient_left_is_higher_x(xs, lip_ids, values)
    higher_x, higher_y = clip_polygon_half(xs, ys, midline_x, keep_lower=False)
    lower_x, lower_y = clip_polygon_half(xs, ys, midline_x, keep_lower=True)
    left_half = (higher_x, higher_y) if left_higher else (lower_x, lower_y)
    right_half = (lower_x, lower_y) if left_higher else (higher_x, higher_y)
    out["left_half_area"] = (shoelace_area(*left_half) / ipd2
                             if len(left_half[0]) >= 3 else float("nan"))
    out["right_half_area"] = (shoelace_area(*right_half) / ipd2
                              if len(right_half[0]) >= 3 else float("nan"))

    for side, upper_key, lower_key in (
        ("left", "LEFT_UPPER_MID_ID", "LEFT_LOWER_MID_ID"),
        ("right", "RIGHT_UPPER_MID_ID", "RIGHT_LOWER_MID_ID"),
    ):
        upper = corrected.get(values[upper_key])
        lower = corrected.get(values[lower_key])
        out[f"{side}_vertical_opening"] = (
            abs(upper[1] - lower[1]) / ipd
            if upper is not None and lower is not None else float("nan"))

    return out, ipd, roll


# ======================================================================
#  Metric definitions
# ======================================================================
# column key -> (title, y label, mode)
# mode: amp_single | amp_lr | peak_single | peak_lr | video_single

METRIC_DEFINITIONS = OrderedDict([
    ("inner_mouth_area",
     ("Inner mouth area, amplitude", "area / IPD\u00b2", "amp_single")),
    (("left_corner_to_midline", "right_corner_to_midline"),
     ("Corner to midline, left against right", "distance / IPD", "amp_lr")),
    (("left_corner_angle", "right_corner_angle"),
     ("Corner angle, left against right", "degrees", "amp_lr")),
    (("left_half_area", "right_half_area"),
     ("Mouth half-area, left against right", "area / IPD\u00b2", "amp_lr")),
    (("left_corner_disp", "right_corner_disp"),
     ("Corner displacement, left against right", "distance / IPD", "amp_lr")),
    (("left_vertical_opening", "right_vertical_opening"),
     ("Vertical lip opening, left against right", "distance / IPD", "amp_lr")),
    ("rise_slope_max",
     ("Steepest rise slope, per opening", "d(area) / d(frame)", "peak_single")),
    ("rise_angle_deg",
     ("Steepest rise angle, per opening", "atan(slope), degrees",
      "peak_single")),
    ("mean_rise_angle_deg",
     ("Mean rise angle, per recording", "atan(slope), degrees",
      "video_single")),
])


# ======================================================================
#  Gathering values per group
# ======================================================================

def compute_amplitudes(df_task, column: str, labels) -> OrderedDict:
    """Peak minus valley, paired in frame order within each recording.

    A peak is paired with the valley that precedes it, which is what the
    picker guarantees when it enforces alternation. Unpaired events at
    either end are dropped rather than paired across a gap.
    """
    out = OrderedDict((label, []) for label in labels)
    if column not in df_task.columns:
        return out
    for _, grp in df_task.groupby(["folder", "video"]):
        grp = grp.sort_values("frame")
        label = grp.iloc[0]["group_label"]
        if label not in out:
            continue
        peaks = grp[grp["point_type"] == "peak"][column].dropna().values
        valleys = grp[grp["point_type"] == "valley"][column].dropna().values
        for k in range(min(len(peaks), len(valleys))):
            out[label].append(float(peaks[k] - valleys[k]))
    return out


def compute_peak_values(df_task, column: str, labels) -> OrderedDict:
    """Values that exist only on peak rows, such as the rise slopes."""
    out = OrderedDict((label, []) for label in labels)
    if column not in df_task.columns:
        return out
    for _, grp in df_task.groupby(["folder", "video"]):
        label = grp.iloc[0]["group_label"]
        if label not in out:
            continue
        vals = grp[grp["point_type"] == "peak"][column].dropna().values
        out[label].extend(float(v) for v in vals)
    return out


def compute_per_video_scalar(df_task, column: str, labels) -> OrderedDict:
    """One value per recording, such as the mean rise angle."""
    out = OrderedDict((label, []) for label in labels)
    if column not in df_task.columns:
        return out
    for _, grp in df_task.groupby(["folder", "video"]):
        label = grp.iloc[0]["group_label"]
        if label not in out:
            continue
        vals = grp[column].dropna().unique()
        if len(vals):
            out[label].append(float(vals[0]))
    return out


def take_best_quartile(values_dict, percentile: float) -> OrderedDict:
    import numpy as np

    out = OrderedDict()
    for label, vals in values_dict.items():
        arr = np.asarray(vals, dtype=float)
        arr = arr[np.isfinite(arr)]
        if not len(arr):
            out[label] = []
            continue
        cutoff = np.percentile(arr, percentile)
        out[label] = arr[arr >= cutoff].tolist()
    return out


def build_values(df_task, mode: str, key, labels, has_types: bool):
    """A dict of values per group, or a (left, right) pair of them."""
    if mode == "amp_single":
        if has_types:
            return compute_amplitudes(df_task, key, labels)
        out = OrderedDict()
        for label in labels:
            rows = df_task[df_task["group_label"] == label]
            out[label] = rows[key].dropna().tolist() if key in rows else []
        return out

    if mode == "amp_lr":
        left_col, right_col = key
        if has_types:
            return (compute_amplitudes(df_task, left_col, labels),
                    compute_amplitudes(df_task, right_col, labels))
        left, right = OrderedDict(), OrderedDict()
        for label in labels:
            rows = df_task[df_task["group_label"] == label]
            left[label] = rows[left_col].dropna().tolist()
            right[label] = rows[right_col].dropna().tolist()
        return left, right

    if mode == "peak_single":
        return compute_peak_values(df_task, key, labels)
    if mode == "peak_lr":
        left_col, right_col = key
        return (compute_peak_values(df_task, left_col, labels),
                compute_peak_values(df_task, right_col, labels))
    if mode == "video_single":
        return compute_per_video_scalar(df_task, key, labels)
    raise ValueError(f"Unknown plot mode: {mode}")


# ======================================================================
#  Plotting
# ======================================================================

def make_strip_plot(ax, values_dict, ylabel: str, title: str, values: dict,
                    central_fn, color="#5ea0d0", bar_color="#2b6cb0",
                    label=None, offset=0.0):
    """One column of points per group, with a summary bar across each."""
    import numpy as np

    labels = list(values_dict.keys())
    rng = np.random.default_rng(values["JITTER_SEED"])
    spread = values["STRIP_JITTER"]

    for i, key in enumerate(labels):
        vals = [v for v in values_dict[key] if np.isfinite(v)]
        if not vals:
            continue
        jitter = rng.uniform(-spread, spread, len(vals))
        ax.scatter([i + offset + j for j in jitter], vals,
                   color=color, s=30, alpha=0.6, edgecolors="none", zorder=3,
                   label=label if i == 0 else None)
        centre = central_fn(vals)
        if np.isfinite(centre):
            ax.plot([i + offset - 0.15, i + offset + 0.15], [centre, centre],
                    color=bar_color, linewidth=2.5, zorder=4)

    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel(ylabel, fontsize=10)
    ax.set_title(title, fontsize=12, fontweight="bold")
    ax.grid(axis="y", alpha=0.3)

    previous = None
    for i, key in enumerate(labels):
        date_part = str(key).split("\n")[0]
        if previous is not None and date_part != previous:
            ax.axvline(i - 0.5, color="#cccccc", linestyle="--", linewidth=0.8)
        previous = date_part
    ax.margins(x=0.03)


def plot_one(payload, mode: str, key, title: str, ylabel: str, task: str,
             out_dir: Path, fig_width: float, values: dict,
             central_fn) -> str:
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(1, 1, figsize=(fig_width, 5))
    full_title = f"{task}: {title}"

    if mode in ("amp_single", "peak_single", "video_single"):
        make_strip_plot(ax, payload, ylabel, full_title, values, central_fn)
        stem = str(key)
    else:
        left_vals, right_vals = payload
        left_col, right_col = key
        stem = f"{left_col}_vs_{right_col}"
        make_strip_plot(ax, left_vals, ylabel, full_title, values, central_fn,
                        color="#4a90d9", bar_color="#1a4f8a",
                        label="Left", offset=-0.15)
        make_strip_plot(ax, right_vals, ylabel, full_title, values, central_fn,
                        color="#d94a4a", bar_color="#8a1a1a",
                        label="Right", offset=+0.15)
        ax.legend(fontsize=9, loc="upper right")

    fig.tight_layout()
    for fmt in values["PLOT_FORMATS"]:
        path = out_dir / f"metrics_{task}_{stem}.{fmt}"
        kwargs = {"format": fmt, "bbox_inches": "tight"}
        if fmt in ("png", "jpg"):
            kwargs["dpi"] = values["PLOT_DPI"]
        fig.savefig(str(path), **kwargs)
    plt.close(fig)
    gc.collect()
    return stem


# ======================================================================
#  Frame-level table
# ======================================================================

def frame_metrics(source_path: Path, values: dict,
                  log: Callable[[str], None] = _noop):
    """One row per frame, with every measure and the grouping columns."""
    import numpy as np
    import pandas as pd

    from .mouth import mid_arc_ids

    needed = set(values["INNER_LIP_IDS"])
    needed.update([values["LEFT_PUPIL_ID"], values["RIGHT_PUPIL_ID"]])
    needed.update(mid_arc_ids(values))

    carried = ["point_type", "mean_rise_angle_deg", "rise_slope_max",
               "rise_angle_deg", "rise_angle_diag_deg", "rise_slope_diag",
               "rise_delta_frame", "rise_delta_area"]

    records = []
    seen_columns = set()
    groups_done = 0

    for chunk in pd.read_csv(source_path, chunksize=CHUNK_ROWS):
        seen_columns.update(chunk.columns)
        sub = chunk[chunk["landmark_id"].isin(needed)]
        if not len(sub):
            continue
        for (folder, video, frame, face_id), grp in sub.groupby(
            ["folder", "video", "frame", "face_id"], dropna=False
        ):
            points = {
                int(lm): (float(x), float(y))
                for lm, x, y in zip(grp["landmark_id"], grp["x_pixel"],
                                    grp["y_pixel"])
                if np.isfinite(x) and np.isfinite(y)
            }
            measures, ipd, roll = compute_metrics(points, values)
            head = grp.iloc[0]
            date, task, stim = parse_video_info(folder, video, values)
            record = {
                "folder": folder, "video": video, "frame": frame,
                "face_id": face_id, "date": date, "task": task, "stim": stim,
                "group_label": f"{date}\n{stim}" if stim else str(date),
                "ipd_px": round(ipd, 2) if np.isfinite(ipd) else None,
                "head_roll_deg": round(roll, 2) if np.isfinite(roll) else None,
            }
            for column in carried:
                if column in grp.columns:
                    record[column] = head[column]
            record.update(measures)
            records.append(record)
            groups_done += 1
            if groups_done % 500 == 0:
                log(f"    {groups_done} frames measured")

    if not records:
        raise RuntimeError(
            "No frame produced a complete set of landmarks. Check that the "
            "input is a stage 3 output and that the inner lip list matches "
            "the mesh used in stage 1.")

    df = pd.DataFrame(records)
    usable = int(df["inner_mouth_area"].notna().sum()) if "inner_mouth_area" in df else 0
    log(f"  {len(df):,} frames, {usable:,} fully measured")
    return df, ("point_type" in seen_columns)


# ======================================================================
#  Whole run
# ======================================================================

def run(values: dict | None = None,
        in_csv: str | None = None,
        out_dir: str | None = None,
        log: Callable[[str], None] = _noop,
        should_stop: Callable[[], bool] = lambda: False) -> dict:
    """Measure every reviewed frame, then draw the plot tree."""
    try:
        import matplotlib
        matplotlib.use("Agg")
    except ImportError as exc:
        raise RuntimeError("Stage 4 needs matplotlib.") from exc

    values = values or st.load()

    source = in_csv or values["EVENTS_CSV"]
    if not source:
        raise RuntimeError("No input. Set Events csv under Paths.")
    source_path = st.resolve_input_path(source, "EVENTS_CSV")

    target = out_dir or values["PLOTS_DIR"]
    if not target:
        raise RuntimeError("No output folder. Set Plots dir under Paths.")
    target_path = Path(str(target).strip()).expanduser()
    if target_path.is_file():
        raise RuntimeError(
            f"Plots dir is set to a file ({target_path}) but needs a folder.")
    try:
        target_path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(
            f"Cannot create the plots folder {target_path}: {exc}") from exc

    started = time.time()
    log(f"Reading {source_path}")
    df, has_types = frame_metrics(source_path, values, log)
    if not has_types:
        log("  no point_type column, so amplitudes fall back to raw values; "
            "this input was not reviewed in the picker")

    csv_out = target_path / "all_frame_metrics.csv"
    df.to_csv(csv_out, index=False)
    log(f"  metrics -> {csv_out}")

    if values["SKIP_PLOTS"]:
        log("  Skip plots is on, stopping before the figures")
        return {"frames": len(df), "csv": str(csv_out), "plots": 0,
                "elapsed_s": round(time.time() - started, 1)}

    tendencies = [(name, CENTRAL_FUNCTIONS[name])
                  for name in values["CENTRAL_TENDENCIES"]
                  if name in CENTRAL_FUNCTIONS]
    if not tendencies:
        raise RuntimeError(
            "No central tendency selected, so there is no bar to draw. "
            "Pick at least one under Plots.")

    tasks = sorted(t for t in df["task"].dropna().unique())
    log(f"  tasks: {', '.join(map(str, tasks))}")

    plots_written = 0
    for task in tasks:
        if should_stop():
            log("Stopped.")
            break
        df_task = df[df["task"] == task]
        if df_task.empty:
            continue

        labels = sorted(df_task["group_label"].unique(), key=group_sort_key)
        fig_width = min(max(10.0, len(labels) * 0.9), 30.0)
        task_dir = safe_name(task)
        log(f"  {task}: {len(labels)} group(s), {len(df_task):,} frames")

        for key, (title, ylabel, mode) in METRIC_DEFINITIONS.items():
            if should_stop():
                break
            columns = [key] if isinstance(key, str) else list(key)
            if not any(c in df_task.columns for c in columns):
                log(f"    skipping {'/'.join(columns)}, not in this input")
                continue

            payload = build_values(df_task, mode, key, labels, has_types)
            best = None
            if values["BEST_QUARTILE"]:
                cut = values["BEST_QUARTILE_PERCENTILE"]
                best = (take_best_quartile(payload, cut)
                        if mode in ("amp_single", "peak_single", "video_single")
                        else (take_best_quartile(payload[0], cut),
                              take_best_quartile(payload[1], cut)))

            for name, function in tendencies:
                full_dir = target_path / name / task_dir
                full_dir.mkdir(parents=True, exist_ok=True)
                plot_one(payload, mode, key, title, ylabel, task_dir,
                         full_dir, fig_width, values, function)
                plots_written += 1

                if best is not None:
                    bq_dir = target_path / "best_quartile" / name / task_dir
                    bq_dir.mkdir(parents=True, exist_ok=True)
                    plot_one(best, mode, key, f"{title}, best quartile",
                             ylabel, task_dir, bq_dir, fig_width, values,
                             function)
                    plots_written += 1

    elapsed = time.time() - started
    log("")
    log(f"  done in {elapsed:.1f}s")
    log(f"  figures   {plots_written} x {len(values['PLOT_FORMATS'])} format(s)")
    log(f"  written   {target_path}")
    return {"frames": len(df), "csv": str(csv_out), "plots": plots_written,
            "elapsed_s": round(elapsed, 1), "out_dir": str(target_path)}
