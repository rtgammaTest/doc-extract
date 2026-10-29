#!/usr/bin/env python3
"""Run the test suite with nothing but the standard library.

`make test` prefers pytest. This exists so that a reviewer who has just cloned
the repo can verify it in one command without installing anything first, which
is the difference between a project someone believes and a project someone
takes your word for.
"""
from __future__ import annotations

import importlib.util
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    passed, failures = 0, []
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        module = load(path)
        for name in sorted(n for n in dir(module) if n.startswith("test_")):
            fn = getattr(module, name)
            if not callable(fn):
                continue
            try:
                fn()
                passed += 1
            except Exception:
                failures.append((path.name, name, traceback.format_exc()))

    for file, name, tb in failures:
        print(f"\nFAIL {file}::{name}\n{tb}", file=sys.stderr)

    total = passed + len(failures)
    print(f"{passed}/{total} passed" + (f", {len(failures)} failed" if failures else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
