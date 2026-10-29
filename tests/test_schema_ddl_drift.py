"""The committed SQL must still match the registry it was generated from.

Two things describe a row: the JSON schema the model is held to, and the table
the row lands in. When they drift you get extractions that validate and then
fail to write, or columns nobody fills -- both of which surface late and look
like something else.

Generating both from one registry removes the drift; this test is what stops
someone editing the generated SQL by hand and putting it back.
"""
from __future__ import annotations

from pathlib import Path

from extractor import schema

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def test_committed_sql_matches_registry():
    stale = []
    for name, render in schema.GENERATED_SQL.items():
        path = SQL_DIR / name
        assert path.exists(), f"{name} has never been generated; run `make sql`"
        if path.read_text(encoding="utf-8") != render("doc_extract"):
            stale.append(name)
    assert not stale, f"regenerate with `make sql`: {stale}"


def test_json_schema_fields_are_a_subset_of_table_columns():
    """Anything the model is asked for must have somewhere to land."""
    columns = set(schema.column_names(schema.EXTRACTION_COLUMNS))
    properties = set(schema.field_record_schema()["properties"])
    assert properties <= columns, f"no column for {sorted(properties - columns)}"


def test_every_model_column_appears_in_the_json_schema():
    """And anything with a column must actually be asked for, or it is dead weight."""
    expected = set(schema.column_names(schema.model_columns()))
    properties = set(schema.field_record_schema()["properties"])
    assert expected == properties


def test_provenance_columns_are_not_asked_of_the_model():
    """The model does not get to tell us which model produced the row."""
    model_supplied = set(schema.column_names(schema.model_columns()))
    for name in ("extractor_version", "model", "prompt_sha", "extracted_at", "doc_id"):
        assert name not in model_supplied


def test_required_json_fields_are_required_in_sql_or_deliberately_nullable():
    """A column required by the schema must not be silently optional downstream."""
    by_name = {c.name: c for c in schema.EXTRACTION_COLUMNS}
    for col in schema.model_columns():
        if col.json_required:
            # value and citation_section are nullable in SQL on purpose: the
            # gate may admit a row whose citation failed to resolve to an
            # offset, and NULL is more honest than an empty string.
            assert col.name in {"field_path", "value", "citation_section"}


def test_citation_offset_is_computed_not_requested():
    offset = next(c for c in schema.EXTRACTION_COLUMNS if c.name == "citation_offset")
    assert offset.source == schema.PIPELINE
    assert offset.json_type is None


def test_dead_letter_carries_the_same_provenance():
    """A rejection you cannot attribute to a prompt and a model is not evidence."""
    dead = set(schema.column_names(schema.DEAD_LETTER_COLUMNS))
    for name in ("extractor_version", "model", "prompt_sha", "invariant", "reason"):
        assert name in dead
