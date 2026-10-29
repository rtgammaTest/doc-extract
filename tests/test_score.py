"""Scoring has to measure extraction, not formatting."""
from __future__ import annotations

from eval.score import (BAD_CITATION, HALLUCINATED_FIELD, MATCH, MISSING_FIELD,
                        UNRESOLVED_DEFINED_TERM, WRONG_VALUE, normalise_value, score)


def test_ratio_forms_are_the_same_answer():
    for written in ("4.00:1.00", "4.0 to 1.0", "4.00 TO 1.00", " 4.00 to 1.00 "):
        assert normalise_value(written) == "4.00 to 1.00"


def test_different_ratios_stay_different():
    assert normalise_value("4.00 to 1.00") != normalise_value("3.00 to 1.00")


def test_missing_value_normalises_to_empty():
    assert normalise_value(None) == ""


GOLD = {
    ("d", "covenants.9_1.threshold"): {"value": "4.00 to 1.00", "citation_section": "Section 9.1"},
    ("d", "covenants.9_1.direction"): {"value": "max", "citation_section": "Section 9.1"},
    ("d", "covenants.9_2.threshold"): {"value": "2.50 to 1.00", "citation_section": "Section 9.2"},
    ("d", "covenants.9_3.defined_term"): {"value": "Consolidated EBITDA", "citation_section": "Section 9.3"},
}


def pred(path, value, citation):
    return {"doc_id": "d", "field_path": path, "value": value, "citation_section": citation}


def outcomes(report):
    return {r.field_path: r.outcome for r in report.rows}


def test_taxonomy_classifies_each_failure_mode():
    report = score(
        GOLD,
        [
            pred("covenants.9_1.threshold", "4.00:1.00", "Section 9.1"),   # match after normalising
            pred("covenants.9_1.direction", "min", "Section 9.1"),         # wrong value
            pred("covenants.9_2.threshold", "2.50 to 1.00", "Section 9.9"),  # bad citation
            pred("covenants.9_9.invented", "x", "Section 9.9"),            # hallucinated
        ],
        [{"doc_id": "d", "field_path": "covenants.9_3.defined_term",
          "invariant": "defined_term_resolves"}],
    )
    got = outcomes(report)
    assert got["covenants.9_1.threshold"] == MATCH
    assert got["covenants.9_1.direction"] == WRONG_VALUE
    assert got["covenants.9_2.threshold"] == BAD_CITATION
    assert got["covenants.9_9.invented"] == HALLUCINATED_FIELD
    assert got["covenants.9_3.defined_term"] == UNRESOLVED_DEFINED_TERM


def test_unresolved_term_is_distinguished_from_a_plain_miss():
    """The hypothesis under test: extraction fails on definition closure."""
    plain = score(GOLD, [], [])
    assert outcomes(plain)["covenants.9_3.defined_term"] == MISSING_FIELD

    gated = score(GOLD, [], [{"doc_id": "d", "field_path": "covenants.9_3.defined_term",
                              "invariant": "defined_term_resolves"}])
    assert outcomes(gated)["covenants.9_3.defined_term"] == UNRESOLVED_DEFINED_TERM


def test_hallucinations_do_not_inflate_the_denominator():
    report = score(GOLD, [pred("covenants.ZZ.nope", "x", "Section 1.1")], [])
    assert report.gold_total == len(GOLD)
    assert report.accuracy == 0.0


def test_perfect_run():
    report = score(
        GOLD,
        [pred(path, v["value"], v["citation_section"]) for (_, path), v in GOLD.items()],
        [],
    )
    assert report.accuracy == 1.0
    assert report.citation_validity == 1.0
