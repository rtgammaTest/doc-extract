"""Render a scored run as markdown.

Every run is committed. One accuracy number says nothing; the trend across
extractor versions is the only thing that tells you whether the work is
converging, and it is also the artefact that makes the LLMOps claim checkable
rather than asserted.
"""
from __future__ import annotations

from datetime import datetime, timezone

TAXONOMY_HELP = {
    "match": "value and citation both correct",
    "wrong_value": "cited the right section, read the wrong number",
    "bad_citation": "right answer, wrong or absent provenance",
    "missing_field": "gold field never extracted",
    "hallucinated_field": "extracted a field with no gold counterpart",
    "unresolved_defined_term": "rejected because a referenced term is not defined here",
}


def render_markdown(report, label: str = "") -> str:
    counts = report.counts
    total = report.gold_total
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        f"# Eval report{f' - {label}' if label else ''}",
        "",
        f"_{when}_",
        "",
        f"**Accuracy: {report.accuracy:.1%}** over {total} gold fields  ",
        f"**Citation validity: {report.citation_validity:.1%}**",
        "",
        "## Failure taxonomy",
        "",
        "| Outcome | Count | Share | Meaning |",
        "|---|---:|---:|---|",
    ]
    for key, help_text in TAXONOMY_HELP.items():
        n = counts.get(key, 0)
        share = f"{n / total:.1%}" if total else "-"
        lines.append(f"| `{key}` | {n} | {share} | {help_text} |")

    lines += ["", "## Rejected at the gate", ""]
    if report.dead_letter:
        lines += ["| Invariant | Rows |", "|---|---:|"]
        lines += [f"| `{k}` | {v} |" for k, v in sorted(report.dead_letter.items())]
    else:
        lines.append("_Nothing was rejected._")

    misses = [r for r in report.rows if r.outcome != "match"]
    lines += ["", "## Every miss", ""]
    if misses:
        lines += ["| Field | Outcome | Expected | Got |", "|---|---|---|---|"]
        for r in misses:
            lines.append(
                f"| `{r.field_path}` | `{r.outcome}` | {r.expected or '-'} | {r.actual or '-'} |"
            )
    else:
        lines.append("_No misses._")

    lines += [
        "",
        "---",
        "",
        "Accuracy alone does not say whether the pipeline is improving. Read the",
        "taxonomy: a run that trades `missing_field` for `wrong_value` has got",
        "worse even if the headline number went up, because a wrong number with a",
        "citation is harder to catch than a gap.",
        "",
    ]
    return "\n".join(lines)
