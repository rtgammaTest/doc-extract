"""Segmentation decides what the model is allowed to see.

The normalisation test is not cosmetic. The source HTML arrives with its curly
quotes already destroyed -- every defined-term delimiter comes through as
U+FFFD -- and defined terms are quote-delimited, so without that substitution
the term lookup finds nothing at all and the whole closure step silently
returns empty.
"""
from __future__ import annotations

from extractor import segment as seg

AGREEMENT = (
    'ARTICLE I. DEFINITIONS\n\n'
    '"Consolidated EBITDA" means net income adjusted for interest and taxes.\n\n'
    '"Consolidated Leverage Ratio" means the ratio of debt to "Consolidated EBITDA".\n\n'
    '"Unused Term" means something nothing refers to.\n\n'
    'ARTICLE IX. COVENANTS\n\n'
    'Section 9.1. The Borrower shall not permit the "Consolidated Leverage Ratio" '
    'to be greater than 4.00 to 1.00.\n'
)


def test_replacement_character_becomes_a_quote():
    assert seg.normalise('�Consolidated EBITDA� means') == '"Consolidated EBITDA" means'


def test_definitions_are_unfindable_without_that_substitution():
    broken = AGREEMENT.replace('"', "�")
    assert seg.find_definitions(broken) == {}
    assert len(seg.find_definitions(seg.normalise(broken))) == 3


def test_section_symbol_and_dashes_normalise():
    assert seg.normalise("§9.1") == "Section 9.1"
    assert seg.normalise("a — b") == "a - b"


def test_find_definitions():
    found = seg.find_definitions(AGREEMENT)
    assert set(found) == {"Consolidated EBITDA", "Consolidated Leverage Ratio", "Unused Term"}


def test_terms_wrapped_across_lines_are_still_found():
    text = '"Consolidated\nEBITDA" means net income.'
    assert "Consolidated EBITDA" in seg.find_definitions(text)


def test_find_sections():
    assert "9.1" in seg.find_sections(AGREEMENT)


def test_closure_pulls_referenced_definitions_and_leaves_the_rest():
    parcel = seg.segment("doc", AGREEMENT, ["9.1"])
    assert "Consolidated Leverage Ratio" in parcel.definitions
    # Reached at depth 2, because the leverage ratio definition cites it.
    assert "Consolidated EBITDA" in parcel.definitions
    # Nothing refers to this, so it never enters the prompt.
    assert "Unused Term" not in parcel.definitions


def test_closure_depth_is_a_real_limit():
    parcel = seg.segment("doc", AGREEMENT, ["9.1"], depth=1)
    assert "Consolidated Leverage Ratio" in parcel.definitions
    assert "Consolidated EBITDA" not in parcel.definitions


def test_prompt_body_carries_both_halves():
    parcel = seg.segment("doc", AGREEMENT, ["9.1"])
    assert "### COVENANT SECTIONS" in parcel.text
    assert "### REFERENCED DEFINITIONS" in parcel.text
    assert "Unused Term" not in parcel.text
