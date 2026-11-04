"""Cross-platform advisory file lock for the ZCode autosync job.

The old Stop-hook used ``flock`` from a bash script. macOS does not ship
``flock`` and Windows does not ship bash, so the lock moved into Python:

* POSIX uses ``fcntl.flock``. The kernel releases it when the process exits.
* Windows uses ``msvcrt.locking`` on the first byte.

The lock file is created next to the autosync log. A stale file does not
block anything: the OS lock disappears with the process that held it.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import IO, Optional


def lock_path_for(state_file: Path) -> Path:
    """Lock file guarding ``state_file``: same directory, ``.lock`` suffix."""
    state_file = Path(state_file)
    return state_file.with_name(state_file.name + ".lock")


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` so readers see either the old or the new file, never half.

    The temp file gets a unique name in the target directory, so two
    writers never share it and ``os.replace`` stays on one filesystem.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


class LockTimeout(RuntimeError):
    """Raised by :meth:`FileLock.__enter__` when the lock is busy."""


class FileLock:
    """A non-blocking file lock with an optional wait timeout."""

    def __init__(self, path: Path, timeout: float = 0.0, poll: float = 0.05):
        self.path = Path(path)
        self.timeout = max(0.0, float(timeout))
        self.poll = max(0.01, float(poll))
        self._handle: Optional[IO[str]] = None

    def acquire(self) -> bool:
        """Try to acquire the lock. Returns False when the timeout expires."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+", encoding="utf-8")
        if os.name == "nt":
            # msvcrt locks a byte range; make sure byte 0 exists.
            handle.seek(0)
            if not handle.read(1):
                handle.write("0")
                handle.flush()
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self._lock_handle(handle)
            except OSError:
                if time.monotonic() >= deadline:
                    handle.close()
                    return False
                time.sleep(self.poll)
                continue
            self._handle = handle
            return True

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            self._unlock_handle(self._handle)
        finally:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> "FileLock":
        if not self.acquire():
            raise LockTimeout(f"another autosync is running: {self.path}")
        return self

    def __exit__(self, *exc_info) -> None:
        self.release()

    @staticmethod
    def _lock_handle(handle: IO[str]) -> None:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock_handle(handle: IO[str]) -> None:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)