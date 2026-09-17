"""
Native library directories on Windows.

Both OpenCV and MediaPipe load compiled extensions that in turn need
DLLs from the environment they were installed into. Since Python 3.8
those directories are not searched automatically, which surfaces as
`ImportError: DLL load failed while importing _framework_bindings` with
nothing in the message pointing at a missing path.

The directories are derived from the interpreter that is running, so
this normally needs no configuration. Set FACEKIT_DLL_DIRS to a
semicolon-separated list to add your own.

Import this before cv2 or mediapipe. It is a no-op off Windows.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_registered: list[str] = []
_done = False


def _candidates() -> list[Path]:
    prefix = Path(sys.prefix)
    exec_prefix = Path(sys.exec_prefix)
    paths = [
        prefix / "Library" / "bin",      # conda
        prefix / "Library" / "mingw-w64" / "bin",
        prefix / "DLLs",
        prefix / "bin",
        exec_prefix / "Library" / "bin",
        Path(sys.executable).parent,
    ]
    for entry in sys.path:
        if entry:
            paths.append(Path(entry) / "cv2")
            paths.append(Path(entry) / "mediapipe")
    for raw in (os.environ.get("FACEKIT_DLL_DIRS") or "").split(";"):
        if raw.strip():
            paths.append(Path(raw.strip()))
    return paths


def register() -> list[str]:
    """Register every candidate directory that exists. Returns those added."""
    global _done
    if _done or not sys.platform.startswith("win"):
        _done = True
        return list(_registered)

    seen = set()
    for path in _candidates():
        try:
            resolved = path.resolve()
        except OSError:
            continue
        key = str(resolved).lower()
        if key in seen or not resolved.is_dir():
            continue
        seen.add(key)
        try:
            os.add_dll_directory(str(resolved))
        except (OSError, AttributeError):
            continue
        _registered.append(str(resolved))

    _done = True
    return list(_registered)


def registered() -> list[str]:
    """Directories registered so far, for the environment panel."""
    return list(_registered)


register()
