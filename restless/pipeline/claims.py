"""Task claims and file locks for several workers sharing one project directory.

A claim is an atomically created directory, so exactly one worker wins it. The owner refreshes a heartbeat file;
a claim whose owner is dead (same host) or silent for `ttl_s` (another host) is taken over, and the unfinished
attempt is left on disk for the new owner to supersede.
"""
from __future__ import annotations

import fcntl
import json
import os
import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path

HOST = socket.gethostname()


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Claims:
    def __init__(self, root: Path, *, ttl_s: float = 900.0, heartbeat_s: float = 30.0) -> None:
        self.root = Path(root)
        self.ttl_s = ttl_s
        self.heartbeat_s = heartbeat_s
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, key: str) -> Path:
        return self.root / key

    def _stale(self, directory: Path) -> bool:
        try:
            owner = json.loads((directory / "owner.json").read_text())
            age = time.time() - (directory / "heartbeat").stat().st_mtime
        except (OSError, ValueError):
            return time.time() - directory.stat().st_mtime > 60  # claimant died between mkdir and writing owner.json
        if owner.get("host") == HOST:
            return not _alive(int(owner["pid"]))
        return age > self.ttl_s

    def try_claim(self, key: str) -> bool:
        directory = self._dir(key)
        if (directory / "done").exists():
            return False
        try:
            directory.mkdir()
        except FileExistsError:
            if (directory / "done").exists() or not self._stale(directory):
                return False
            # Take over through a rename so two takers cannot both win.
            grave = self.root / f".stale-{key}-{os.getpid()}-{time.time_ns()}"
            try:
                directory.rename(grave)
            except OSError:
                return False
            try:
                directory.mkdir()
            except FileExistsError:
                return False
        (directory / "owner.json").write_text(json.dumps({"host": HOST, "pid": os.getpid(), "started": time.time()}))
        (directory / "heartbeat").touch()
        return True

    @contextmanager
    def hold(self, key: str):
        """Keep the heartbeat fresh while the caller works on a claimed key."""
        stop = threading.Event()
        beat = self._dir(key) / "heartbeat"

        def loop() -> None:
            while not stop.wait(self.heartbeat_s):
                try:
                    beat.touch()
                except OSError:
                    return
        thread = threading.Thread(target=loop, daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=2)

    def finish(self, key: str, *, ok: bool) -> None:
        directory = self._dir(key)
        if ok:
            (directory / "done").write_text(str(time.time()))
        else:  # release so a later pass (or --retry-failed) can take it again
            (directory / "failed").write_text(str(time.time()))
            try:
                directory.rename(self.root / f".failed-{key}-{time.time_ns()}")
            except OSError:
                pass

    def done(self, key: str) -> bool:
        return (self._dir(key) / "done").exists()


@contextmanager
def file_lock(path: Path, *, timeout_s: float = 7200.0):
    """Exclusive advisory lock that also works across processes on one host and NFS-free local disks."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+")
    started = time.monotonic()
    try:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() - started > timeout_s:
                    raise TimeoutError(f"timed out waiting for lock {path}")
                time.sleep(0.2)
        yield
    finally:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            handle.close()
