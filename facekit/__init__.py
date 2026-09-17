"""
facekit: facial movement analysis for clinical video, behind one local
interface.

Four stages, each usable on its own:

    landmarks   468 or 478 mesh points per frame, from a folder of video
    mouth       inner-mouth area, normalised by inter-pupil distance
    events      the openings, reviewed by hand, and how fast each one opened
    metrics     left against right geometry, and the figures

Nothing leaves the machine it runs on.
"""

from __future__ import annotations

import sys

from . import _winsetup  # noqa: F401  registers native library paths on Windows

__version__ = "1.0.0"
__author__ = "Luca Pivetti"

STAGE_MODULES = ("landmarks", "mouth", "events", "metrics")


def env_report() -> dict:
    """Python version, per-stage dependency status, native library paths.

    Imported lazily so this works in an environment where none of the
    optional dependencies are installed, which is exactly when it is
    most useful.
    """
    from . import events, landmarks, metrics, mouth
    from . import settings as st

    stages = [module.requirements() for module in
              (landmarks, mouth, events, metrics)]
    return {
        "version": __version__,
        "python": sys.version.split()[0],
        "python_ok": (3, 10) <= sys.version_info[:2] <= (3, 12),
        "platform": sys.platform,
        "stages": stages,
        "all_ok": all(s["ok"] for s in stages),
        "config_dir": str(st.CONFIG_DIR),
        "settings_file": str(st.SETTINGS_FILE),
        "dll_dirs": _winsetup.registered(),
    }


def __getattr__(name):
    """Expose the stage modules without importing their dependencies early."""
    if name in STAGE_MODULES:
        import importlib

        return importlib.import_module(f".{name}", __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["env_report", "__version__", *STAGE_MODULES]
