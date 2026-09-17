"""
Stage 2: the mouth-area time series.

For every frame, the inner-lip contour is turned into an area by the
shoelace formula and divided by the squared inter-pupil distance, which
removes the participant's distance from the camera. The series is then
downsampled: one representative frame in N carries the average over its
own window, so nothing is thrown away, only condensed.

The input can be several million rows, so it is read in chunks in two
passes and never held whole: the first pass needs only the lip and iris
landmarks, and the second copies through the representative frames.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from . import settings as st

CHUNK_ROWS = 500_000

KEY = ["folder", "video", "frame", "face_id"]


def _noop(msg: str) -> None:
    pass


def requirements() -> dict:
    missing = []
    for module, package in (("pandas", "pandas"), ("numpy", "numpy")):
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    return {
        "stage": "mouth",
        "ok": not missing,
        "missing": missing,
        "install": f"pip install {' '.join(missing)}" if missing else "",
    }


def mid_arc_ids(values: dict) -> list[int]:
    return [values["RIGHT_UPPER_MID_ID"], values["RIGHT_LOWER_MID_ID"],
            values["LEFT_UPPER_MID_ID"], values["LEFT_LOWER_MID_ID"]]


def needed_landmarks(values: dict) -> list[int]:
    """Every landmark any later stage reads."""
    ids = set(values["INNER_LIP_IDS"])
    ids.update([values["LEFT_PUPIL_ID"], values["RIGHT_PUPIL_ID"]])
    ids.update(mid_arc_ids(values))
    return sorted(ids)


# ======================================================================
#  Geometry
# ======================================================================

def shoelace_area(xs, ys):
    """Polygon area for one or many polygons.

    `xs` and `ys` are (n_polygons, n_vertices) arrays, or 1-D for a
    single polygon. Vertices must be in loop order; the result is
    meaningless for a self-intersecting outline.
    """
    import numpy as np

    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    single = xs.ndim == 1
    if single:
        xs = xs[None, :]
        ys = ys[None, :]
    x_next = np.roll(xs, -1, axis=1)
    y_next = np.roll(ys, -1, axis=1)
    twice = np.sum(xs * y_next - x_next * ys, axis=1)
    out = np.abs(twice) / 2.0
    return float(out[0]) if single else out


# ======================================================================
#  Per-frame measures
# ======================================================================

def _frame_table(chunks, values: dict, log: Callable[[str], None]):
    """Wide table of per-frame area, inter-pupil distance and normalised area."""
    import numpy as np
    import pandas as pd

    lip_ids = list(values["INNER_LIP_IDS"])
    left_id = values["LEFT_PUPIL_ID"]
    right_id = values["RIGHT_PUPIL_ID"]
    wanted = set(lip_ids) | {left_id, right_id}

    kept = []
    for chunk in chunks:
        sub = chunk[chunk["landmark_id"].isin(wanted)]
        if len(sub):
            kept.append(sub)
    if not kept:
        raise RuntimeError(
            "No lip or iris landmarks in the input. If Refine landmarks was "
            "off during stage 1 there are no iris points to normalise by.")

    lip = pd.concat(kept, ignore_index=True)
    del kept

    xs = lip.pivot_table(index=KEY, columns="landmark_id",
                         values="x_pixel", aggfunc="first")
    ys = lip.pivot_table(index=KEY, columns="landmark_id",
                         values="y_pixel", aggfunc="first")
    del lip

    have_lips = [i for i in lip_ids if i in xs.columns]
    if len(have_lips) < len(lip_ids):
        missing = sorted(set(lip_ids) - set(have_lips))
        raise RuntimeError(
            f"Inner lip landmarks missing from the input: {missing}. "
            f"The contour has to be complete for an area to mean anything.")

    area = shoelace_area(xs[lip_ids].to_numpy(), ys[lip_ids].to_numpy())

    out = pd.DataFrame(index=xs.index)
    out["inner_mouth_area_px2"] = np.round(area, 2)

    if left_id in xs.columns and right_id in xs.columns:
        dx = xs[left_id].to_numpy() - xs[right_id].to_numpy()
        dy = ys[left_id].to_numpy() - ys[right_id].to_numpy()
        ipd = np.sqrt(dx ** 2 + dy ** 2)
    else:
        raise RuntimeError(
            f"Iris landmarks {left_id} and {right_id} are not in the input. "
            f"Rerun stage 1 with Refine landmarks on.")
    out["interpupil_dist_px"] = np.round(ipd, 2)

    if values["NORMALISE_BY"] == "ipd_squared":
        with np.errstate(divide="ignore", invalid="ignore"):
            norm = out["inner_mouth_area_px2"] / out["interpupil_dist_px"] ** 2
        norm = norm.replace([np.inf, -np.inf], np.nan)
    else:
        norm = out["inner_mouth_area_px2"].astype(float)
    out["mouth_area_norm"] = norm.round(6)

    out = out.reset_index()
    usable = int(out["mouth_area_norm"].notna().sum())
    log(f"  {len(out):,} frames measured, {usable:,} with a usable area")
    if usable == 0:
        raise RuntimeError(
            "Every frame came out unusable. The usual cause is a landmark "
            "table written with Refine landmarks off, or an inner lip list "
            "that does not match the mesh.")
    return out


def _window_average(frames, values: dict, log: Callable[[str], None]):
    """Average each measure over the window its representative frame leads."""
    import pandas as pd

    every = values["KEEP_EVERY_N"]
    frames = frames.copy()
    frames["frame"] = pd.to_numeric(frames["frame"], errors="coerce")
    frames = frames.dropna(subset=["frame"])
    frames["repr_frame"] = (frames["frame"] // every * every).astype(int)

    grouped = (
        frames.groupby(["folder", "video", "face_id", "repr_frame"], dropna=False)
        .agg(inner_mouth_area_avg_px2=("inner_mouth_area_px2", "mean"),
             interpupil_dist_avg_px=("interpupil_dist_px", "mean"),
             mouth_area_norm_avg=("mouth_area_norm", "mean"),
             frames_in_window=("mouth_area_norm", "size"),
             frames_usable=("mouth_area_norm", "count"))
        .reset_index()
        .rename(columns={"repr_frame": "frame"})
    )
    grouped["inner_mouth_area_avg_px2"] = grouped["inner_mouth_area_avg_px2"].round(2)
    grouped["interpupil_dist_avg_px"] = grouped["interpupil_dist_avg_px"].round(2)
    grouped["mouth_area_norm_avg"] = grouped["mouth_area_norm_avg"].round(6)

    log(f"  {len(grouped):,} representative frames after averaging over "
        f"windows of {every}")
    return grouped


# ======================================================================
#  Whole run
# ======================================================================

def run(values: dict | None = None,
        in_csv: str | None = None,
        out_csv: str | None = None,
        log: Callable[[str], None] = _noop,
        should_stop: Callable[[], bool] = lambda: False) -> dict:
    """Read a landmark table, write the downsampled table with mouth area."""
    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError("Stage 2 needs pandas.") from exc

    values = values or st.load()

    source = in_csv or values["LANDMARKS_CSV"]
    if not source:
        raise RuntimeError(
            "No input. Set Landmarks csv under Paths, or pass a CSV path.")
    source_path = st.resolve_input_path(source, "LANDMARKS_CSV")

    target = out_csv or values["MOUTH_CSV"]
    if not target:
        raise RuntimeError("No output. Set Mouth csv under Paths, or pass -o.")
    target_path = st.resolve_output_path(target, "MOUTH_CSV")
    if target_path.name != Path(str(target)).name:
        log(f"Mouth csv named a folder, writing to {target_path}")

    started = time.time()
    log(f"Reading {source_path}")

    def chunks():
        return pd.read_csv(source_path, chunksize=CHUNK_ROWS)

    frames = _frame_table(chunks(), values, log)
    if should_stop():
        log("Stopped.")
        return {"stopped": True}

    averaged = _window_average(frames, values, log)
    del frames

    every = values["KEEP_EVERY_N"]
    keep_ids = (None if values["KEEP_LANDMARKS"] == "all"
                else set(needed_landmarks(values)))
    if keep_ids is not None:
        log(f"  carrying {len(keep_ids)} landmark(s) per frame "
            f"(Keep landmarks is set to needed)")

    written = 0
    header_done = False
    with open(target_path, "w", newline="", encoding="utf-8") as out_file:
        for chunk in chunks():
            if should_stop():
                log("Stopped.")
                break
            frame_no = pd.to_numeric(chunk["frame"], errors="coerce")
            sub = chunk[frame_no % every == 0]
            if keep_ids is not None:
                sub = sub[sub["landmark_id"].isin(keep_ids)]
            if not len(sub):
                continue
            merged = sub.merge(averaged, on=KEY, how="left")
            merged.to_csv(out_file, index=False, header=not header_done)
            header_done = True
            written += len(merged)

    if not written:
        raise RuntimeError(
            f"No rows written. With Keep every n set to {every}, no frame "
            f"number in the input was a multiple of it.")

    elapsed = time.time() - started
    log("")
    log(f"  done in {elapsed:.1f}s")
    log(f"  rows      {written:,}")
    log(f"  written   {target_path}")

    return {"rows": written, "frames": len(averaged),
            "elapsed_s": round(elapsed, 1), "csv": str(target_path)}
