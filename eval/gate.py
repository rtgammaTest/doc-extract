"""Fail the build when the eval regresses.

A quality gate on a non-deterministic component is the thing almost nobody
builds, and it is the whole reason the eval is worth running automatically. Two
thresholds, because they catch different failures: a floor catches a change that
breaks extraction outright, and a regression bound catches the slow drift where
each prompt tweak costs half a point and nobody notices for a month.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Gate the build on eval results.")
    ap.add_argument("--report", type=Path, required=True, help="report JSON from score.py")
    ap.add_argument("--baseline", type=Path, default=None,
                    help="previous committed report JSON; skipped if absent")
    ap.add_argument("--min-accuracy", type=float, default=0.80)
    ap.add_argument("--max-regression", type=float, default=0.03)
    args = ap.parse_args(argv)

    report = json.loads(args.report.read_text(encoding="utf-8"))
    accuracy = float(report["accuracy"])
    failures: list[str] = []

    if accuracy < args.min_accuracy:
        failures.append(f"accuracy {accuracy:.1%} is below the floor of {args.min_accuracy:.1%}")

    if args.baseline and args.baseline.exists():
        before = float(json.loads(args.baseline.read_text(encoding="utf-8"))["accuracy"])
        drop = before - accuracy
        if drop > args.max_regression:
            failures.append(
                f"accuracy fell {drop:.1%} against the baseline "
                f"({before:.1%} -> {accuracy:.1%}), limit {args.max_regression:.1%}"
            )
        else:
            print(f"baseline {before:.1%} -> {accuracy:.1%}")
    else:
        print("no baseline report; floor check only")

    if failures:
        for line in failures:
            print(f"FAIL: {line}", file=sys.stderr)
        return 1

    print(f"PASS: accuracy {accuracy:.1%}, citations {float(report['citation_validity']):.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
