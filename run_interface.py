#!/usr/bin/env python3
"""
Start the facekit interface.

Equivalent to `python -m facekit`. This file exists so the tool can be
started by double-clicking, and so a path-less clone still runs:

    python run_interface.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.resolve()))

from facekit.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["serve"] + sys.argv[1:]))
