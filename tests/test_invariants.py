"""The gate is the part of this system that is allowed to be strict.

Every rule here is tested in both directions -- a case it must admit and a case
it must reject -- because a rule that never fires is indistinguishable from a
rule that does not work.
"""
from __future__ import annotations

from extractor import invariants as inv

TEXT = (
    "Section 9.1. The Borrower shall not permit the Consolidated Leverage Ratio, "
    "as of the last day of each fiscal quarter, to be greater than 4.00 to 1.00.\n"
    "Section 9.2. Minimum coverage of 2.50 to 1.00."
)
CTX = inv.Context(text=TEXT, definitions=frozenset({"Consolidated EBITDA"}))


def rec(path, value, citation="Section 9.1", **extra):
    return {"field_path": path, "value": value, "citation_section": citation, **extra}


def names(violations):
    return sorted(v.invariant for v in violations)


# -- field_path -------------------------------------------------------------
def test_well_formed_field_path_is_accepted():
    assert inv.field_path_well_formed([rec("covenants.9_1.threshold", "4.00 to 1.00")], CTX) == []


def test_malformed_field_path_is_rejected():
    out = inv.field_path_well_formed([rec("threshold", "4.00 to 1.00")], CTX)
    assert names(out) == ["field_path_well_formed"]


# -- citation ---------------------------------------------------------------
def test_citation_present_in_source_is_accepted():
    assert inv.citation_resolves([rec("covenants.9_1.threshold", "x", "Section 9.1")], CTX) == []


def test_invented_citation_is_rejected():
    out = inv.citation_resolves([rec("covenants.9_9.threshold", "x", "Section 9.9")], CTX)
    assert names(out) == ["citation_resolves"]
    assert "Section 9.9" in out[0].reason


# -- ratio ------------------------------------------------------------------
def test_canonical_ratio_is_accepted():
    assert inv.ratio_threshold_format([rec("covenants.9_1.threshold", "4.00 to 1.00")], CTX) == []


def test_colon_ratio_is_rejected():
    out = inv.ratio_threshold_format([rec("covenants.9_1.threshold", "4.00:1.00")], CTX)
    assert names(out) == ["ratio_threshold_format"]


def test_single_decimal_ratio_is_rejected():
    out = inv.ratio_threshold_format([rec("covenants.9_1.threshold", "4.0 to 1.0")], CTX)
    assert names(out) == ["ratio_threshold_format"]


def test_currency_threshold_is_not_treated_as_a_ratio():
    assert inv.ratio_threshold_format([rec("covenants.9_1.threshold", "$50,000,000")], CTX) == []


# -- direction --------------------------------------------------------------
def test_direction_enum():
    assert inv.direction_in_enum([rec("covenants.9_1.direction", "max")], CTX) == []
    out = inv.direction_in_enum([rec("covenants.9_1.direction", "maximum")], CTX)
    assert names(out) == ["direction_in_enum"]


# -- maintenance ------------------------------------------------------------
def test_maintenance_with_frequency_is_accepted():
    records = [
        rec("covenants.9_1.type", "maintenance"),
        rec("covenants.9_1.test_frequency", "each fiscal quarter"),
    ]
    assert inv.maintenance_has_frequency(records, CTX) == []


def test_maintenance_without_frequency_rejects_the_whole_covenant():
    records = [
        rec("covenants.9_1.type", "maintenance"),
        rec("covenants.9_1.threshold", "4.00 to 1.00"),
    ]
    out = inv.maintenance_has_frequency(records, CTX)
    assert names(out) == ["maintenance_has_frequency"]
    assert out[0].covenant_scope is True

    verdict = inv.gate(records, CTX)
    assert verdict.accepted == [], "a covenant that cannot be evaluated is not partly good"
    assert len(verdict.rejected) == 2


def test_incurrence_needs_no_frequency():
    assert inv.maintenance_has_frequency([rec("covenants.9_3.type", "incurrence")], CTX) == []


# -- step-downs -------------------------------------------------------------
def test_ascending_step_downs_accepted():
    r = [rec("covenants.9_1.step_down_dates", "2024-06-30, 2024-12-31, 2025-06-30")]
    assert inv.step_down_dates_ordered(r, CTX) == []


def test_unordered_step_downs_rejected():
    r = [rec("covenants.9_1.step_down_dates", "2025-06-30, 2024-12-31")]
    assert names(inv.step_down_dates_ordered(r, CTX)) == ["step_down_dates_ordered"]


def test_repeated_step_down_rejected():
    r = [rec("covenants.9_1.step_down_dates", "2024-06-30, 2024-06-30")]
    assert names(inv.step_down_dates_ordered(r, CTX)) == ["step_down_dates_ordered"]


def test_non_iso_step_down_rejected():
    r = [rec("covenants.9_1.step_down_dates", "30 June 2024")]
    assert names(inv.step_down_dates_ordered(r, CTX)) == ["step_down_dates_ordered"]


# -- defined terms ----------------------------------------------------------
def test_known_defined_term_accepted():
    r = [rec("covenants.9_3.defined_term", "Consolidated EBITDA")]
    assert inv.defined_term_resolves(r, CTX) == []


def test_term_defined_in_another_agreement_is_still_rejected_here():
    """The finding covenant-definition-graph left behind.

    'Available Amount' is defined in both agreements in the gold set and means
    different things in each. A term is only resolvable against the document it
    was read from.
    """
    r = [rec("covenants.9_3.defined_term", "Available Amount")]
    assert names(inv.defined_term_resolves(r, CTX)) == ["defined_term_resolves"]


def test_wrapped_term_is_normalised_before_lookup():
    r = [rec("covenants.9_3.defined_term", "Consolidated\n  EBITDA")]
    assert inv.defined_term_resolves(r, CTX) == []


# -- gate -------------------------------------------------------------------
def test_gate_rejects_only_the_offending_field():
    records = [
        rec("covenants.9_1.threshold", "4.00:1.00"),
        rec("covenants.9_1.direction", "max"),
    ]
    verdict = inv.gate(records, CTX)
    assert [r["field_path"] for r in verdict.accepted] == ["covenants.9_1.direction"]
    assert len(verdict.rejected) == 1
    assert verdict.rejected[0][1].invariant == "ratio_threshold_format"


def test_gate_reports_every_failure_not_just_the_first():
    records = [
        rec("covenants.9_1.threshold", "4.00:1.00"),
        rec("covenants.9_1.direction", "sideways"),
    ]
    assert names(inv.gate(records, CTX).violations) == [
        "direction_in_enum", "ratio_threshold_format",
    ]


def test_clean_records_all_pass():
    records = [
        rec("covenants.9_1.threshold", "4.00 to 1.00"),
        rec("covenants.9_1.direction", "max"),
        rec("covenants.9_1.type", "maintenance"),
        rec("covenants.9_1.test_frequency", "each fiscal quarter"),
    ]
    verdict = inv.gate(records, CTX)
    assert len(verdict.accepted) == 4
    assert verdict.rejected == []
