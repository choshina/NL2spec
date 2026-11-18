"""Cross-process MATLAB checkout limiter controlled by MATLAB_MAX_CONCURRENT."""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def matlab_slot(timeout_s: float = 3600.0):
    limit = max(1, int(os.environ.get("MATLAB_MAX_CONCURRENT", "1")))
    root = Path(os.environ.get("MATLAB_SLOT_DIR", "/tmp/respstl_matlab_slots"))
    root.mkdir(parents=True, exist_ok=True)
    started = time.monotonic(); acquired = None
    while acquired is None:
        for index in range(limit):
            candidate = root / f"slot_{index}"
            try:
                candidate.mkdir(); (candidate / "owner").write_text(str(os.getpid())); acquired = candidate; break
            except FileExistsError:
                # Recover a checkout left behind by a killed worker.
                try:
                    owner = int((candidate / "owner").read_text().strip())
                    os.kill(owner, 0)
                except ProcessLookupError:
                    try: (candidate / "owner").unlink(missing_ok=True); candidate.rmdir()
                    except OSError: pass
                except (OSError, ValueError):
                    pass
                continue
        if acquired is None:
            if time.monotonic() - started > timeout_s: raise TimeoutError("timed out waiting for MATLAB slot")
            time.sleep(0.25)
    try:
        yield
    finally:
        try: (acquired / "owner").unlink(missing_ok=True); acquired.rmdir()
        except OSError: pass
