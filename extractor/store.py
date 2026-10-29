"""Where accepted rows and rejected rows go.

Both backends are append-only. There is no update path and no delete path, by
construction rather than by convention: re-extracting a document inserts rows
carrying a new ``extractor_version`` against the same immutable source, and
``current`` is a view that picks the latest per ``(doc_id, field_path)``.

That is the same requirement GIPS 2.A.21 puts on estimated valuations -- a
preliminary number must be reconcilable to the final one, which you cannot do
if the preliminary number was overwritten. Applied to model output, it means a
better model next quarter improves what the system believes without destroying
the evidence for what it believed before.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, Sequence

from .schema import TABLE_DEAD_LETTER, TABLE_EXTRACTIONS

Row = dict[str, Any]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store(Protocol):
    def write_extractions(self, rows: Sequence[Row]) -> int: ...
    def write_dead_letter(self, rows: Sequence[Row]) -> int: ...


@dataclass
class JsonlStore:
    """Local append-only files. Same columns, same ordering, no project needed.

    This is also the documented fallback if BigQuery is cut for time: the eval
    reads either backend, so the story survives the simplification.
    """

    out_dir: Path

    def __post_init__(self) -> None:
        self.out_dir = Path(self.out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def _append(self, name: str, rows: Sequence[Row]) -> int:
        if not rows:
            return 0
        path = self.out_dir / f"{name}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        return len(rows)

    def write_extractions(self, rows: Sequence[Row]) -> int:
        return self._append(TABLE_EXTRACTIONS, rows)

    def write_dead_letter(self, rows: Sequence[Row]) -> int:
        return self._append(TABLE_DEAD_LETTER, rows)

    def read_extractions(self) -> list[Row]:
        return self._read(TABLE_EXTRACTIONS)

    def read_dead_letter(self) -> list[Row]:
        return self._read(TABLE_DEAD_LETTER)

    def _read(self, name: str) -> list[Row]:
        path = self.out_dir / f"{name}.jsonl"
        if not path.exists():
            return []
        with path.open(encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def current(self) -> list[Row]:
        """The JSONL equivalent of the `current` view: latest row per key."""
        latest: dict[tuple[str, str], Row] = {}
        for row in self.read_extractions():
            key = (row.get("doc_id", ""), row.get("field_path", ""))
            seen = latest.get(key)
            if seen is None or row.get("extracted_at", "") >= seen.get("extracted_at", ""):
                latest[key] = row
        return [latest[k] for k in sorted(latest)]


@dataclass
class BigQueryStore:
    """BigQuery, imported lazily so nothing else depends on the client library."""

    project: str
    dataset: str

    def _client(self):
        from google.cloud import bigquery  # noqa: PLC0415

        return bigquery.Client(project=self.project)

    def _insert(self, table: str, rows: Sequence[Row]) -> int:
        if not rows:
            return 0
        client = self._client()
        table_id = f"{self.project}.{self.dataset}.{table}"
        errors = client.insert_rows_json(table_id, list(rows))
        if errors:
            raise RuntimeError(f"BigQuery rejected rows for {table_id}: {errors}")
        return len(rows)

    def write_extractions(self, rows: Sequence[Row]) -> int:
        return self._insert(TABLE_EXTRACTIONS, rows)

    def write_dead_letter(self, rows: Sequence[Row]) -> int:
        return self._insert(TABLE_DEAD_LETTER, rows)


def build_store(config) -> Store:
    if config.store_backend == "bigquery":
        return BigQueryStore(project=config.project, dataset=config.dataset)
    return JsonlStore(out_dir=config.out_dir)
