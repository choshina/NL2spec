"""Content-addressed labelled trace cache used by T7/T9 and replay artifacts."""

from __future__ import annotations

import csv
import hashlib
import os
from pathlib import Path
from typing import Iterable, Sequence

from restless.pipeline.claims import file_lock


def trace_hash(values: Sequence[float]) -> str:
    return hashlib.sha256(",".join(f"{float(v):.17g}" for v in values).encode()).hexdigest()


class TraceCache:
    def __init__(self, root: Path, instance_id: str) -> None:
        self.directory = Path(root) / instance_id
        self.directory.mkdir(parents=True, exist_ok=True)
        self.index = self.directory / "index.csv"

    def put(self, trace: Sequence[float], *, generator: str, seed: int,
            oracle_label: int, round_id: int | str = "") -> str:
        digest = trace_hash(trace)
        path = self.directory / f"{digest}.csv"
        if not path.exists():
            temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            temporary.write_text(",".join(f"{float(v):.17g}" for v in trace) + "\n")
            temporary.replace(path)
        # The index is read-modify-write, and several methods of one instance append to it concurrently.
        with file_lock(self.directory / ".index.lock"):
            rows = self.rows()
            key = (digest, generator, str(seed), str(round_id))
            if not any((r["hash"], r["generator"], r["seed"], r["round_id"]) == key for r in rows):
                rows.append({"hash": digest, "generator": generator, "seed": str(seed),
                             "oracle_label": str(int(oracle_label)), "round_id": str(round_id),
                             "length": str(len(trace)), "file": path.name})
                self._write(rows)
        return digest

    def rows(self) -> list[dict[str, str]]:
        if not self.index.exists(): return []
        with self.index.open(newline="") as handle: return list(csv.DictReader(handle))

    def load(self, *, seed: int | None = None) -> list[dict[str, object]]:
        result = []
        for row in self.rows():
            if seed is not None and int(row["seed"]) != seed: continue
            values = [float(v) for v in (self.directory / row["file"]).read_text().strip().split(",")]
            result.append({**row, "trace": values, "oracle_label": int(row["oracle_label"])})
        return result

    def _write(self, rows: Iterable[dict[str, str]]) -> None:
        fields = ["hash", "generator", "seed", "oracle_label", "round_id", "length", "file"]
        temporary = self.index.with_suffix(f".{os.getpid()}.tmp")
        with temporary.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
        temporary.replace(self.index)

