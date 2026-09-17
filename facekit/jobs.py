"""
Running a stage in the background, so a long batch streams progress.

A landmark run over a few dozen recordings takes minutes to hours. The
interface starts one through `start()` and then polls `state()`, which
returns whatever log lines have appeared since the cursor it was given.
One job runs at a time; asking for a second while one is live is
refused rather than queued, because both would write the same CSV.
"""

from __future__ import annotations

import threading
import time
import traceback
from typing import Callable

_MAX_LINES = 4000


class Job:
    """One background stage run."""

    def __init__(self, name: str, function: Callable, kwargs: dict):
        self.name = name
        self._function = function
        self._kwargs = kwargs
        self._lines: list[str] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.started_at = time.time()
        self.finished_at: float | None = None
        self.status = "running"
        self.error: str | None = None
        self.result: dict | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    # -- logging ----------------------------------------------------
    def log(self, message: str) -> None:
        with self._lock:
            self._lines.append(str(message))
            if len(self._lines) > _MAX_LINES:
                drop = len(self._lines) - _MAX_LINES
                del self._lines[:drop]
                self._lines.insert(
                    0, f"[{drop} earlier lines dropped]")

    def lines_since(self, cursor: int) -> tuple[list[str], int]:
        with self._lock:
            cursor = max(0, min(cursor, len(self._lines)))
            return list(self._lines[cursor:]), len(self._lines)

    # -- control ----------------------------------------------------
    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.log("Stop requested, finishing the current file.")

    def should_stop(self) -> bool:
        return self._stop.is_set()

    @property
    def running(self) -> bool:
        return self.status == "running"

    @property
    def elapsed(self) -> float:
        end = self.finished_at or time.time()
        return end - self.started_at

    # -- body -------------------------------------------------------
    def _run(self) -> None:
        try:
            self.result = self._function(
                log=self.log, should_stop=self.should_stop, **self._kwargs)
            self.status = "stopped" if self.should_stop() else "done"
        except Exception as exc:
            self.status = "failed"
            self.error = f"{type(exc).__name__}: {exc}"
            self.log("")
            self.log(f"FAILED  {self.error}")
            for line in self._hints(exc):
                self.log(f"        {line}")
            for line in traceback.format_exc().splitlines()[-12:]:
                self.log(f"  {line}")
        finally:
            self.finished_at = time.time()

    @staticmethod
    def _hints(exc: Exception) -> list[str]:
        """Plain-language causes for errors whose own message is opaque.

        A permission error is the main one. Windows reports opening a
        directory for writing as one, and the message names only the
        path, so on its own it reads as a rights problem when it usually
        is not.
        """
        if isinstance(exc, PermissionError):
            return [
                "The usual causes, in order of likelihood:",
                "  1. the path names a folder where a file is needed. Add a",
                "     filename to the end of it, such as ...\\landmarks.csv",
                "  2. the file is open in Excel, which locks it on Windows.",
                "     Close it and run again.",
                "  3. the folder is managed by OneDrive or covered by",
                "     Windows controlled folder access. Write somewhere",
                "     under your own user folder instead.",
            ]
        if isinstance(exc, MemoryError):
            return ["Raise Keep every n, or split the input folders across "
                    "separate runs."]
        return []

    def state(self, cursor: int = 0) -> dict:
        lines, new_cursor = self.lines_since(cursor)
        return {
            "name": self.name,
            "status": self.status,
            "running": self.running,
            "elapsed_s": round(self.elapsed, 1),
            "lines": lines,
            "cursor": new_cursor,
            "error": self.error,
            "result": self.result,
        }


class Runner:
    """Holds the one live job."""

    def __init__(self):
        self._job: Job | None = None
        self._lock = threading.Lock()

    def start(self, name: str, function: Callable, **kwargs) -> Job:
        with self._lock:
            if self._job is not None and self._job.running:
                raise RuntimeError(
                    f"{self._job.name} is still running. Wait for it or stop "
                    f"it before starting {name}.")
            job = Job(name, function, kwargs)
            self._job = job
        job.start()
        return job

    @property
    def job(self) -> Job | None:
        return self._job

    def state(self, cursor: int = 0) -> dict:
        if self._job is None:
            return {"name": None, "status": "idle", "running": False,
                    "lines": [], "cursor": 0, "error": None, "result": None,
                    "elapsed_s": 0.0}
        return self._job.state(cursor)

    def stop(self) -> bool:
        if self._job is not None and self._job.running:
            self._job.stop()
            return True
        return False
