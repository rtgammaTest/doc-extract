"""Run the pipeline over local files. The same code path Cloud Run takes.

    python -m extractor.cli data/six_flags_2015.txt
    MODEL_BACKEND=vertex PROJECT=my-project python -m extractor.cli doc.txt
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import Config
from .model import build_model
from .pipeline import run
from .store import build_store


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Extract covenants from local documents.")
    ap.add_argument("paths", nargs="+", type=Path, help="text files to extract")
    ap.add_argument("--sections", nargs="*", default=None,
                    help="restrict to these section numbers, e.g. 9.1 9.2")
    ap.add_argument("--out-dir", type=Path, default=None, help="override OUT_DIR")
    args = ap.parse_args(argv)

    config = Config.from_env()
    if args.out_dir:
        config = Config(**{**config.__dict__, "out_dir": args.out_dir})

    model, store = build_model(config), build_store(config)

    failures = 0
    for path in args.paths:
        if not path.exists():
            print(f"missing: {path}", file=sys.stderr)
            failures += 1
            continue
        result = run(
            path.stem, path.read_text(encoding="utf-8", errors="replace"),
            model=model, store=store, config=config, section_numbers=args.sections,
        )
        print(json.dumps(result.as_dict(), indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
