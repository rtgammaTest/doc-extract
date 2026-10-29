"""Write the DDL from extractor/schema.py.

The generated files are committed so that a schema change shows up as a
reviewable SQL diff rather than as a surprise at deploy time. Run `make sql`
after changing the registry; `tests/test_schema_ddl_drift.py` fails the build
if you forget.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from extractor.schema import GENERATED_SQL  # noqa: E402

SQL_DIR = ROOT / "sql"


def render(dataset: str) -> dict[str, str]:
    return {name: fn(dataset) for name, fn in GENERATED_SQL.items()}


def main() -> int:
    dataset = os.environ.get("DATASET", "doc_extract")
    changed = []
    for name, body in render(dataset).items():
        path = SQL_DIR / name
        if not path.exists() or path.read_text(encoding="utf-8") != body:
            path.write_text(body, encoding="utf-8")
            changed.append(name)
    print(f"wrote {len(changed)} file(s): {', '.join(changed) if changed else 'none, already current'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
