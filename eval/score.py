"""Score extractions against hand-verified ground truth.

This is the part that matters. An extraction pipeline without an eval is a
demo: it produces output nobody can grade, which means nobody can tell whether
changing the prompt made it better or worse.

The failure taxonomy is the point of the exercise rather than the headline
accuracy number. `covenant-definition-graph` predicted that extraction fails on
definition closure rather than on numbers. ``unresolved_defined_term`` is where
that prediction is either confirmed or killed.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eval.report import render_markdown  # noqa: E402
from extractor.store import JsonlStore  # noqa: E402

_RATIO_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(?::|\bto\b)\s*(\d+(?:\.\d+)?)$", re.IGNORECASE)

MATCH = "match"
WRONG_VALUE = "wrong_value"
BAD_CITATION = "bad_citation"
MISSING_FIELD = "missing_field"
HALLUCINATED_FIELD = "hallucinated_field"
UNRESOLVED_DEFINED_TERM = "unresolved_defined_term"

TAXONOMY = (
    MATCH, WRONG_VALUE, BAD_CITATION, MISSING_FIELD,
    HALLUCINATED_FIELD, UNRESOLVED_DEFINED_TERM,
)


def normalise_value(value: str | None) -> str:
    """Compare what the document meant, not how it was typed.

    `4.00:1.00`, `4.0 to 1.0` and `4.00 to 1.00` are the same covenant. Scoring
    them as three different answers measures formatting, not extraction.
    """
    if value is None:
        return ""
    text = " ".join(str(value).split()).strip().lower()
    ratio = _RATIO_RE.match(text)
    if ratio:
        return f"{float(ratio.group(1)):.2f} to {float(ratio.group(2)):.2f}"
    return text


@dataclass
class Scored:
    doc_id: str
    field_path: str
    outcome: str
    expected: str = ""
    actual: str = ""
    note: str = ""


@dataclass
class Report:
    rows: list[Scored] = field(default_factory=list)
    dead_letter: Counter = field(default_factory=Counter)

    @property
    def counts(self) -> Counter:
        return Counter(r.outcome for r in self.rows)

    @property
    def gold_total(self) -> int:
        return sum(1 for r in self.rows if r.outcome != HALLUCINATED_FIELD)

    @property
    def accuracy(self) -> float:
        total = self.gold_total
        return (self.counts[MATCH] / total) if total else 0.0

    @property
    def citation_validity(self) -> float:
        graded = [r for r in self.rows if r.outcome in (MATCH, WRONG_VALUE, BAD_CITATION)]
        if not graded:
            return 0.0
        good = sum(1 for r in graded if r.outcome != BAD_CITATION)
        return good / len(graded)

    def as_dict(self) -> dict:
        return {
            "accuracy": round(self.accuracy, 4),
            "citation_validity": round(self.citation_validity, 4),
            "gold_total": self.gold_total,
            "counts": {k: self.counts.get(k, 0) for k in TAXONOMY},
            "dead_letter": dict(sorted(self.dead_letter.items())),
            "rows": [r.__dict__ for r in self.rows],
        }


def load_gold(gold_dir: Path) -> dict[tuple[str, str], dict[str, str]]:
    gold: dict[tuple[str, str], dict[str, str]] = {}
    for path in sorted(gold_dir.glob("*.csv")):
        with path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                key = (row["doc_id"].strip(), row["field_path"].strip())
                gold[key] = {
                    "value": row.get("value", "").strip(),
                    "citation_section": row.get("citation_section", "").strip(),
                }
    return gold


def score(gold: dict, predictions: list[dict], dead_letter: list[dict]) -> Report:
    report = Report()
    by_key = {(p.get("doc_id", ""), p.get("field_path", "")): p for p in predictions}

    for row in dead_letter:
        report.dead_letter[row.get("invariant", "unknown")] += 1

    unresolved = {
        (row.get("doc_id", ""), row.get("field_path", ""))
        for row in dead_letter
        if row.get("invariant") == "defined_term_resolves"
    }

    for key, want in sorted(gold.items()):
        doc_id, field_path = key
        got = by_key.get(key)
        if got is None:
            outcome = UNRESOLVED_DEFINED_TERM if key in unresolved else MISSING_FIELD
            note = "rejected by defined_term_resolves" if key in unresolved else ""
            report.rows.append(Scored(doc_id, field_path, outcome, want["value"], "", note))
            continue

        if want["citation_section"] and got.get("citation_section") != want["citation_section"]:
            report.rows.append(Scored(
                doc_id, field_path, BAD_CITATION, want["citation_section"],
                str(got.get("citation_section") or ""), "cited the wrong section",
            ))
            continue

        if normalise_value(got.get("value")) == normalise_value(want["value"]):
            report.rows.append(Scored(doc_id, field_path, MATCH, want["value"], str(got.get("value") or "")))
        else:
            report.rows.append(Scored(
                doc_id, field_path, WRONG_VALUE, want["value"], str(got.get("value") or ""),
            ))

    for key, got in sorted(by_key.items()):
        if key not in gold:
            report.rows.append(Scored(
                key[0], key[1], HALLUCINATED_FIELD, "", str(got.get("value") or ""),
                "not present in gold",
            ))
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Score extractions against gold data.")
    ap.add_argument("--gold", type=Path, default=ROOT / "eval" / "gold")
    ap.add_argument("--out-dir", type=Path, default=ROOT / "out",
                    help="directory holding extractions.jsonl / dead_letter.jsonl")
    ap.add_argument("--report", type=Path, default=ROOT / "eval" / "reports" / "latest.md")
    ap.add_argument("--json", dest="json_out", type=Path, default=None,
                    help="defaults to the report path with a .json suffix")
    ap.add_argument("--label", default="", help="free-text label for the report header")
    args = ap.parse_args(argv)

    gold = load_gold(args.gold)
    if not gold:
        print(f"no gold CSVs in {args.gold}", file=sys.stderr)
        return 2

    store = JsonlStore(out_dir=args.out_dir)
    report = score(gold, store.current(), store.read_dead_letter())

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render_markdown(report, label=args.label), encoding="utf-8")

    json_out = args.json_out or args.report.with_suffix(".json")
    json_out.write_text(json.dumps(report.as_dict(), indent=2) + "\n", encoding="utf-8")

    print(f"accuracy {report.accuracy:.1%} over {report.gold_total} gold fields "
          f"· citations {report.citation_validity:.1%} · report {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
