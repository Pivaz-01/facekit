"""
Stage 3: finding the openings, and measuring how fast each one opened.

A valley is a closed mouth, a peak is an open one, and the interval
between them is a rise. The rise is measured as the steepest point of a
smoothing cubic spline's derivative across that interval, not as the
straight line from valley to peak: a real opening accelerates, so the
diagonal underestimates it by however much the movement is non-linear.
Both numbers are written out, so the choice stays inspectable.

Detection and slope live here rather than in the browser. The picker
asks this module what it would find and draws the answer, which is the
only way the figure on screen and the numbers in the CSV are guaranteed
to be the same.
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Callable

from . import settings as st

CHUNK_ROWS = 500_000

RISE_COLUMNS = [
    "rise_valley_frame", "rise_delta_frame", "rise_delta_area",
    "rise_slope_diag", "rise_slope_max", "rise_slope_max_frame",
    "rise_angle_deg", "rise_angle_diag_deg", "rise_oversmoothed",
]


def _noop(msg: str) -> None:
    pass


def requirements() -> dict:
    missing = []
    for module, package in (("pandas", "pandas"), ("numpy", "numpy"),
                            ("scipy", "scipy")):
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    return {
        "stage": "events",
        "ok": not missing,
        "missing": missing,
        "install": f"pip install {' '.join(missing)}" if missing else "",
    }


# ======================================================================
#  Reading the series
# ======================================================================

def load_series(csv_path: Path, log: Callable[[str], None] = _noop) -> list[dict]:
    """One entry per recording: its frames and its normalised mouth area."""
    import pandas as pd

    wanted = ["folder", "video", "frame", "mouth_area_norm_avg"]
    try:
        df = pd.read_csv(csv_path, usecols=wanted)
    except ValueError as exc:
        raise RuntimeError(
            f"{csv_path.name} does not look like a stage 2 output: {exc}. "
            f"Run the mouth-area stage first.") from exc

    df = df.drop_duplicates(subset=["folder", "video", "frame"])
    df = df.dropna(subset=["frame"])

    series = []
    for (folder, video), grp in df.groupby(["folder", "video"], sort=True):
        grp = grp.sort_values("frame")
        series.append({
            "folder": folder,
            "video": video,
            "frames": [int(f) for f in grp["frame"].tolist()],
            "areas": [None if pd.isna(a) else float(a)
                      for a in grp["mouth_area_norm_avg"].tolist()],
        })
    log(f"  {len(series)} recording(s), "
        f"{sum(len(s['frames']) for s in series):,} representative frames")
    return series


# ======================================================================
#  Smoothing
# ======================================================================

def moving_average(values, window: int):
    """Centred moving average, edges handled by clamping."""
    import numpy as np

    y = np.asarray(values, dtype=float)
    if window < 2:
        return y.copy()
    half = window // 2
    padded = np.pad(y, half, mode="edge")
    kernel = np.ones(2 * half + 1) / (2 * half + 1)
    return np.convolve(padded, kernel, mode="valid")[: len(y)]


def auto_window(n: int) -> int:
    """An odd smoothing window scaled to recording length."""
    win = int(n * 0.025)
    win = max(3, min(21, win | 1))
    return win


def resolve_window(n: int, values: dict, override: int | None = None) -> int:
    win = override if override is not None else values["SMOOTHING_WINDOW"]
    if not win:
        return auto_window(n)
    return int(win) | 1


# ======================================================================
#  Detection
# ======================================================================

def _extrema(y, min_distance: int, min_prominence: float, find_peaks_: bool):
    """Prominence-filtered local extrema, most prominent first, then spaced."""
    import numpy as np
    from scipy.signal import find_peaks

    sign = 1.0 if find_peaks_ else -1.0
    signal = sign * np.asarray(y, dtype=float)
    finite = np.isfinite(signal)
    if finite.sum() < 3:
        return []
    filled = signal.copy()
    if not finite.all():
        idx = np.arange(len(filled))
        filled[~finite] = np.interp(idx[~finite], idx[finite], signal[finite])

    found, props = find_peaks(filled, prominence=min_prominence,
                              distance=max(1, min_distance))
    order = np.argsort(-props["prominences"])
    return sorted(int(found[i]) for i in order)


def enforce_alternating(y, peaks: list[int], valleys: list[int]):
    """Keep peaks and valleys strictly alternating.

    Within any run of same-type events, the most extreme one survives.
    Without this a noisy plateau yields several peaks in a row and no
    rise can be measured across them.
    """
    events = [{"i": i, "type": "peak", "val": y[i]} for i in peaks]
    events += [{"i": i, "type": "valley", "val": y[i]} for i in valleys]
    events.sort(key=lambda e: e["i"])
    if len(events) <= 1:
        return list(peaks), list(valleys)

    kept = [events[0]]
    for event in events[1:]:
        previous = kept[-1]
        if event["type"] != previous["type"]:
            kept.append(event)
            continue
        better = (event["val"] > previous["val"] if event["type"] == "peak"
                  else event["val"] < previous["val"])
        if better:
            kept[-1] = event
    return ([e["i"] for e in kept if e["type"] == "peak"],
            [e["i"] for e in kept if e["type"] == "valley"])


def detect(areas, values: dict, window: int | None = None,
           sensitivity: int | None = None) -> dict:
    """Peaks and valleys as frame *indices* into the series.

    Detection runs on the smoothed signal; each event is then snapped to
    the most extreme raw sample inside the smoothing window, so a marker
    always sits on a real measurement.
    """
    import numpy as np

    raw = np.asarray([np.nan if a is None else a for a in areas], dtype=float)
    n = len(raw)
    if n < 5:
        return {"peaks": [], "valleys": [], "window": 0, "smoothed": []}

    win = resolve_window(n, values, window)
    sens = sensitivity if sensitivity is not None else values["SENSITIVITY"]
    smoothed = moving_average(raw, win)

    finite = smoothed[np.isfinite(smoothed)]
    if len(finite) < 3:
        return {"peaks": [], "valleys": [], "window": win,
                "smoothed": smoothed.tolist()}

    q25, q75 = np.percentile(finite, [25, 75])
    iqr = q75 - q25
    span = float(np.nanmax(finite) - np.nanmin(finite))
    factor = 1.0 - (sens / 100.0) * 0.95
    prominence = max(iqr * factor, span * values["MIN_PROMINENCE_FRAC"])
    prominence = max(prominence, 1e-12)
    distance = max(2, round(n * values["MIN_PEAK_DISTANCE_FRAC"]))

    peaks = _extrema(smoothed, distance, prominence, True)
    valleys = _extrema(smoothed, distance, prominence, False)
    if values["ENFORCE_ALTERNATING"]:
        peaks, valleys = enforce_alternating(smoothed, peaks, valleys)

    half = win // 2
    peaks = _snap(raw, peaks, half, want_max=True)
    valleys = _snap(raw, valleys, half, want_max=False)

    return {"peaks": sorted(peaks), "valleys": sorted(valleys),
            "window": win, "smoothed": _jsonable(smoothed)}


def _snap(raw, indices: list[int], half: int, want_max: bool) -> list[int]:
    import numpy as np

    n = len(raw)
    out = []
    for i in indices:
        lo, hi = max(0, i - half), min(n, i + half + 1)
        window = raw[lo:hi]
        if not np.isfinite(window).any():
            out.append(i)
            continue
        pick = np.nanargmax(window) if want_max else np.nanargmin(window)
        out.append(lo + int(pick))
    return sorted(set(out))


def _jsonable(array) -> list:
    import numpy as np

    return [None if not np.isfinite(v) else round(float(v), 8) for v in array]


# ======================================================================
#  Rise slope
# ======================================================================

def fit_spline(frames, areas, values: dict):
    """A smoothing cubic spline whose derivative is the rise rate.

    The smoothing factor s trades fidelity against a usable derivative.
    At s=0 the spline interpolates every sample and its derivative
    chases the noise; too large and the spline stops following the
    movement at all.

    The automatic value estimates the noise from *second* differences,
    robustly. First differences do not work: a real opening has a large
    first difference, so estimating noise from them treats the movement
    as noise and flattens the very edge being measured. Second
    differences of a smooth curve are small whatever its slope, and
    taking their median absolute deviation rather than their variance
    keeps one bad frame from inflating the estimate.

    For white noise of standard deviation sigma, the second differences
    have variance 6 * sigma**2, and MAD is 0.6745 * sigma for a normal
    distribution. UnivariateSpline wants s as a target sum of squared
    residuals, hence n * sigma**2.
    """
    import numpy as np
    from scipy.interpolate import UnivariateSpline

    x = np.asarray(frames, dtype=float)
    y = np.asarray([np.nan if a is None else a for a in areas], dtype=float)
    keep = np.isfinite(x) & np.isfinite(y)
    x, y = x[keep], y[keep]
    if len(x) < 5:
        return None

    s = values["SPLINE_SMOOTHING"]
    if not s:
        second = np.diff(y, n=2)
        if len(second):
            mad = float(np.median(np.abs(second - np.median(second))))
            sigma = mad / 0.6745 / np.sqrt(6.0)
            if sigma <= 0:
                sigma = float(np.std(second)) / np.sqrt(6.0)
        else:
            sigma = 0.0
        s = max(len(x) * sigma ** 2, 1e-12)

    try:
        return UnivariateSpline(x, y, k=3, s=s)
    except Exception:
        try:
            return UnivariateSpline(x, y, k=3, s=0)
        except Exception:
            return None


def max_slope_on_interval(spline, frame_start: float, frame_end: float,
                          samples: int = 200):
    """The steepest point of the spline derivative across one rise."""
    import numpy as np

    if spline is None or frame_end <= frame_start:
        return float("nan"), float("nan")
    xs = np.linspace(frame_start, frame_end, samples)
    dy = spline.derivative()(xs)
    best = int(np.argmax(dy))
    return float(dy[best]), float(xs[best])


def compute_rises(frames, areas, peaks: list[int], valleys: list[int],
                  values: dict) -> list[dict]:
    """One entry per valley-to-peak transition, in frame order."""
    import numpy as np

    spline = fit_spline(frames, areas, values)
    samples = values["SLOPE_SAMPLES"]

    events = [{"i": i, "type": "peak"} for i in peaks]
    events += [{"i": i, "type": "valley"} for i in valleys]
    events.sort(key=lambda e: e["i"])

    rises = []
    for first, second in zip(events, events[1:]):
        if first["type"] != "valley" or second["type"] != "peak":
            continue
        v_i, p_i = first["i"], second["i"]
        v_frame, p_frame = frames[v_i], frames[p_i]
        v_area = areas[v_i]
        p_area = areas[p_i]
        if v_area is None or p_area is None:
            continue
        d_frame = p_frame - v_frame
        if d_frame <= 0:
            continue
        d_area = p_area - v_area

        slope_diag = d_area / d_frame
        slope_max, frame_at_max = max_slope_on_interval(
            spline, v_frame, p_frame, samples)

        # The mean value theorem: the maximum of the derivative across an
        # interval cannot be below the average rate of change across it.
        # When it is, the spline is not following the data, which means
        # the smoothing factor is too high for this recording. Recorded
        # rather than corrected, because the honest fix is to lower
        # Spline smoothing, not to substitute a different number.
        under = (math.isfinite(slope_max) and slope_max < slope_diag)

        rises.append({
            "valley_frame": int(v_frame),
            "valley_area": float(v_area),
            "peak_frame": int(p_frame),
            "peak_area": float(p_area),
            "delta_frame": int(d_frame),
            "delta_area": float(d_area),
            "slope_diag": float(slope_diag),
            "slope_max": float(slope_max),
            "slope_max_frame": (float(frame_at_max)
                                if math.isfinite(frame_at_max) else None),
            "angle_deg": (math.degrees(math.atan(slope_max))
                          if math.isfinite(slope_max) else None),
            "angle_diag_deg": math.degrees(math.atan(slope_diag)),
            "oversmoothed": bool(under),
        })

    fitted = None
    if spline is not None:
        xs = np.asarray(frames, dtype=float)
        try:
            fitted = _jsonable(spline(xs))
        except Exception:
            fitted = None
    return rises, fitted


def analyse(entry: dict, values: dict, window: int | None = None,
            sensitivity: int | None = None) -> dict:
    """Everything the picker needs for one recording, in one call."""
    found = detect(entry["areas"], values, window, sensitivity)
    peak_frames = [entry["frames"][i] for i in found["peaks"]]
    valley_frames = [entry["frames"][i] for i in found["valleys"]]
    rises, fitted = compute_rises(
        entry["frames"], entry["areas"], found["peaks"], found["valleys"],
        values)

    angles = [r["angle_deg"] for r in rises
              if r["angle_deg"] is not None and math.isfinite(r["angle_deg"])]
    return {
        "peaks": peak_frames,
        "valleys": valley_frames,
        "window": found["window"],
        "smoothed": found["smoothed"],
        "fitted": fitted,
        "rises": rises,
        "mean_angle_deg": (sum(angles) / len(angles)) if angles else None,
    }


# ======================================================================
#  Export
# ======================================================================

def export(selections: dict, values: dict,
           in_csv: str | None = None, out_csv: str | None = None,
           log: Callable[[str], None] = _noop) -> dict:
    """Write the reviewed events out as a filtered, annotated CSV.

    `selections` maps "folder\\x00video" to
    {"points": [{"frame": int, "type": "peak"|"valley"|"manual"}, ...],
     "rises": [...]}.
    Only the selected frames survive into the output, each tagged with
    its type, and peak rows additionally carry the rise that produced
    them.
    """
    import pandas as pd

    source = in_csv or values["MOUTH_CSV"]
    target = out_csv or values["EVENTS_CSV"]
    if not source:
        raise RuntimeError("No input. Set Mouth csv under Paths.")
    if not target:
        raise RuntimeError("No output. Set Events csv under Paths.")

    source_path = st.resolve_input_path(source, "MOUTH_CSV")
    target_path = st.resolve_output_path(target, "EVENTS_CSV")

    type_of: dict = {}
    rise_at: dict = {}
    mean_angle: dict = {}

    for key, payload in selections.items():
        folder, _, video = key.partition("\x00")
        points = payload.get("points", [])
        rises = payload.get("rises", [])
        for point in points:
            type_of[(folder, video, int(point["frame"]))] = point["type"]

        angles = []
        for rise in rises:
            peak_frame = int(rise["peak_frame"])
            rise_at[(folder, video, peak_frame)] = {
                "rise_valley_frame": rise["valley_frame"],
                "rise_delta_frame": rise["delta_frame"],
                "rise_delta_area": rise["delta_area"],
                "rise_slope_diag": rise["slope_diag"],
                "rise_slope_max": rise["slope_max"],
                "rise_slope_max_frame": rise["slope_max_frame"],
                "rise_angle_deg": rise["angle_deg"],
                "rise_angle_diag_deg": rise["angle_diag_deg"],
                "rise_oversmoothed": rise.get("oversmoothed", False),
            }
            if rise["angle_deg"] is not None:
                angles.append(rise["angle_deg"])
        if angles:
            mean_angle[(folder, video)] = sum(angles) / len(angles)

    if not type_of:
        return {"rows": 0, "points": 0, "rises": 0,
                "message": "Nothing selected, so nothing was saved."}

    started = time.time()
    written = 0
    header_done = False

    with open(target_path, "w", newline="", encoding="utf-8") as out_file:
        for chunk in pd.read_csv(source_path, chunksize=CHUNK_ROWS):
            keys = list(zip(chunk["folder"], chunk["video"],
                            chunk["frame"].astype("Int64")))
            mask = [k in type_of for k in keys]
            matched = chunk.loc[mask].copy()
            if not len(matched):
                continue

            triples = list(zip(matched["folder"], matched["video"],
                               matched["frame"].astype(int)))
            matched["point_type"] = [type_of[t] for t in triples]
            matched["mean_rise_angle_deg"] = [
                mean_angle.get((f, v)) for f, v, _ in triples]
            for column in RISE_COLUMNS:
                matched[column] = [
                    (rise_at[t][column] if t in rise_at else None)
                    for t in triples]

            matched.to_csv(out_file, index=False, header=not header_done)
            header_done = True
            written += len(matched)

    if not written:
        raise RuntimeError(
            f"None of the {len(type_of)} selected frames were found in "
            f"{source_path.name}. This happens when the picker was loaded "
            f"from a different file than the one configured under Paths.")

    elapsed = time.time() - started
    log(f"  {written:,} rows -> {target_path}")
    return {
        "rows": written,
        "points": len(type_of),
        "rises": len(rise_at),
        "elapsed_s": round(elapsed, 1),
        "csv": str(target_path),
        "message": (f"Saved {len(type_of)} points and {len(rise_at)} rises "
                    f"as {written:,} rows to {target_path.name}"),
    }


# ======================================================================
#  Headless run
# ======================================================================

def run(values: dict | None = None,
        in_csv: str | None = None,
        out_csv: str | None = None,
        log: Callable[[str], None] = _noop,
        should_stop: Callable[[], bool] = lambda: False) -> dict:
    """Detect events for every recording and export without review.

    This is the unreviewed path. It is here for batch work and for
    getting a first look at a dataset, not for results: automatic
    detection puts markers on swallows, speech and camera wobble as
    readily as on the movement you asked for. Open the picker.
    """
    values = values or st.load()
    source = in_csv or values["MOUTH_CSV"]
    if not source:
        raise RuntimeError("No input. Set Mouth csv under Paths.")
    source_path = st.resolve_input_path(source, "MOUTH_CSV")

    log(f"Reading {source_path}")
    series = load_series(source_path, log)

    selections = {}
    total_points = 0
    total_oversmoothed = 0
    for index, entry in enumerate(series, 1):
        if should_stop():
            log("Stopped.")
            break
        result = analyse(entry, values)
        points = ([{"frame": f, "type": "peak"} for f in result["peaks"]]
                  + [{"frame": f, "type": "valley"} for f in result["valleys"]])
        points.sort(key=lambda p: p["frame"])
        selections[f"{entry['folder']}\x00{entry['video']}"] = {
            "points": points, "rises": result["rises"]}
        total_points += len(points)
        flagged = sum(1 for r in result["rises"] if r.get("oversmoothed"))
        total_oversmoothed += flagged
        note = f", {flagged} over-smoothed" if flagged else ""
        log(f"  [{index}/{len(series)}] {entry['video']}: "
            f"{len(result['peaks'])} peaks, {len(result['valleys'])} valleys, "
            f"{len(result['rises'])} rises{note}")

    log("")
    log(f"  {total_points} points detected across {len(selections)} recording(s)")
    if total_oversmoothed:
        log(f"  WARNING {total_oversmoothed} rise(s) have a spline slope below "
            f"their own diagonal, which means the spline is not following the "
            f"data. Lower Spline smoothing, or read rise_slope_diag instead.")
    log("  this output is unreviewed; open the picker before reporting from it")
    return export(selections, values, in_csv=source, out_csv=out_csv, log=log)
