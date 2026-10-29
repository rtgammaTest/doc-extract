"""Single source of truth for the extraction contract.

Two things have to agree about what a row looks like: the JSON schema the model
is held to, and the SQL table the rows land in.  Nothing stops those drifting
apart, and when they do you get extractions that pass validation and then fail
to write, or columns nobody ever fills.  Both failures surface late and look
like something else.

So both are generated from the registry below.  ``sql/generate.py`` writes the
DDL from it, the generated files are committed so diffs are reviewable, and
``tests/test_schema_ddl_drift.py`` fails the build when the committed SQL stops
matching the registry.

The same bug, one layer down, is what `covenant-definition-graph` found between
two credit agreements: the same defined term meaning different things in two
places.  Here the blast radius is smaller and the fix is mechanical.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: A column the model supplies in its structured output.
MODEL = "model"
#: A column the pipeline supplies -- provenance, or something it computes.
PIPELINE = "pipeline"


@dataclass(frozen=True)
class Column:
    """One column of a stored row, and (optionally) one field of the JSON schema."""

    name: str
    sql_type: str
    sql_required: bool
    source: str
    comment: str
    json_type: str | None = None
    json_required: bool = False
    json_constraints: dict[str, Any] = field(default_factory=dict)

    @property
    def from_model(self) -> bool:
        return self.source == MODEL


# --------------------------------------------------------------------------
# The accepted-extraction row.
#
# The three columns that make this a versioned store rather than a cache are
# extractor_version, model and prompt_sha.  Without them a re-run silently
# replaces what the system believed last quarter.
# --------------------------------------------------------------------------
EXTRACTION_COLUMNS: tuple[Column, ...] = (
    Column(
        "doc_id", "STRING", True, PIPELINE,
        "object path in raw/ -- the immutable source this row was derived from",
    ),
    Column(
        "field_path", "STRING", True, MODEL,
        'dotted path, e.g. "covenants.9_1.threshold"',
        json_type="string", json_required=True,
    ),
    Column(
        "value", "STRING", False, MODEL,
        "extracted value, as it reads in the document",
        json_type="string", json_required=True,
    ),
    Column(
        "citation_section", "STRING", False, MODEL,
        'section the value was read from, e.g. "Section 9.1"',
        json_type="string", json_required=True,
    ),
    Column(
        "citation_page", "INT64", False, MODEL,
        "1-indexed page, where the source carries pagination",
        json_type="integer",
    ),
    Column(
        "citation_offset", "INT64", False, PIPELINE,
        "character offset into the normalised text; computed, never asked for",
    ),
    Column(
        "confidence", "FLOAT64", False, MODEL,
        "the model's own confidence -- recorded, never used as a gate",
        json_type="number", json_constraints={"minimum": 0, "maximum": 1},
    ),
    Column(
        "extractor_version", "STRING", True, PIPELINE,
        "semver of the extractor that produced this row",
    ),
    Column(
        "model", "STRING", True, PIPELINE,
        'model id, e.g. "gemini-2.5-flash"',
    ),
    Column(
        "prompt_sha", "STRING", True, PIPELINE,
        "sha256 of the prompt template used",
    ),
    Column(
        "extracted_at", "TIMESTAMP", True, PIPELINE,
        "when this extraction ran",
    ),
)


# --------------------------------------------------------------------------
# The dead-letter row.
#
# A rejected extraction is written here with its reason, not dropped: the
# failures are the dataset the next extractor version is improved against.
# --------------------------------------------------------------------------
DEAD_LETTER_COLUMNS: tuple[Column, ...] = (
    Column("doc_id", "STRING", True, PIPELINE, "object path in raw/"),
    Column("field_path", "STRING", False, PIPELINE, "null when the whole response was rejected"),
    Column("invariant", "STRING", True, PIPELINE, "name of the rule that rejected it"),
    Column("reason", "STRING", True, PIPELINE, "human-readable failure, including the offending value"),
    Column("raw_response", "STRING", False, PIPELINE, "the model output verbatim, for replay"),
    Column("extractor_version", "STRING", True, PIPELINE, "semver of the extractor"),
    Column("model", "STRING", True, PIPELINE, "model id"),
    Column("prompt_sha", "STRING", True, PIPELINE, "sha256 of the prompt template"),
    Column("rejected_at", "TIMESTAMP", True, PIPELINE, "when this rejection happened"),
)


TABLE_EXTRACTIONS = "extractions"
TABLE_DEAD_LETTER = "dead_letter"
VIEW_CURRENT = "current"


def model_columns() -> tuple[Column, ...]:
    """The columns the model is responsible for supplying."""
    return tuple(c for c in EXTRACTION_COLUMNS if c.from_model)


def column_names(columns: tuple[Column, ...]) -> list[str]:
    return [c.name for c in columns]


# --------------------------------------------------------------------------
# JSON schema -- the contract with the model
# --------------------------------------------------------------------------
def field_record_schema() -> dict[str, Any]:
    """One extracted field.

    Every field is an object rather than a bare scalar, because the citation has
    to travel with the value.  A value without a citation is an assertion; a
    value with one is evidence.
    """
    properties: dict[str, Any] = {}
    required: list[str] = []
    for col in model_columns():
        prop: dict[str, Any] = {"type": col.json_type, "description": col.comment}
        prop.update(col.json_constraints)
        properties[col.name] = prop
        if col.json_required:
            required.append(col.name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def extraction_response_schema() -> dict[str, Any]:
    """The full structured-output schema the model response must satisfy."""
    return {
        "type": "object",
        "properties": {"fields": {"type": "array", "items": field_record_schema()}},
        "required": ["fields"],
        "additionalProperties": False,
    }


# --------------------------------------------------------------------------
# DDL -- the contract with the store
# --------------------------------------------------------------------------
_HEADER = (
    "-- Generated by sql/generate.py from extractor/schema.py. Do not edit by hand.\n"
    "-- Regenerate with `make sql`; tests/test_schema_ddl_drift.py fails if this\n"
    "-- file stops matching the registry.\n"
)


def _render_columns(columns: tuple[Column, ...]) -> str:
    width_name = max(len(c.name) for c in columns)
    width_type = max(len(c.sql_type) for c in columns)
    specs = []
    for col in columns:
        null = "NOT NULL" if col.sql_required else "        "
        specs.append(f"  {col.name.ljust(width_name)}  {col.sql_type.ljust(width_type)}  {null}")
    width_spec = max(len(s.rstrip()) for s in specs)
    lines = []
    for i, (spec, col) in enumerate(zip(specs, columns)):
        comma = "," if i < len(columns) - 1 else ""
        lines.append(f"{(spec.rstrip() + comma).ljust(width_spec + 1)}  -- {col.comment}")
    return "\n".join(lines)


def ddl_extractions(dataset: str = "doc_extract") -> str:
    return (
        f"{_HEADER}\n"
        f"CREATE TABLE IF NOT EXISTS {dataset}.{TABLE_EXTRACTIONS} (\n"
        f"{_render_columns(EXTRACTION_COLUMNS)}\n"
        f")\n"
        f"PARTITION BY DATE(extracted_at)\n"
        f"CLUSTER BY doc_id, field_path;\n"
    )


def ddl_dead_letter(dataset: str = "doc_extract") -> str:
    return (
        f"{_HEADER}\n"
        f"CREATE TABLE IF NOT EXISTS {dataset}.{TABLE_DEAD_LETTER} (\n"
        f"{_render_columns(DEAD_LETTER_COLUMNS)}\n"
        f")\n"
        f"PARTITION BY DATE(rejected_at)\n"
        f"CLUSTER BY doc_id, invariant;\n"
    )


def ddl_current_view(dataset: str = "doc_extract") -> str:
    """Latest accepted version per (doc_id, field_path).

    Nothing is updated in place, so "current" is a view over an append-only
    table rather than a table that gets overwritten.  Every earlier answer stays
    queryable, which is the whole point.
    """
    return (
        f"{_HEADER}\n"
        f"CREATE OR REPLACE VIEW {dataset}.{VIEW_CURRENT} AS\n"
        f"SELECT * EXCEPT(rn) FROM (\n"
        f"  SELECT *, ROW_NUMBER() OVER (\n"
        f"    PARTITION BY doc_id, field_path ORDER BY extracted_at DESC\n"
        f"  ) rn\n"
        f"  FROM {dataset}.{TABLE_EXTRACTIONS}\n"
        f") WHERE rn = 1;\n"
    )


GENERATED_SQL: dict[str, Any] = {
    "001_create_extractions.sql": ddl_extractions,
    "002_create_dead_letter.sql": ddl_dead_letter,
    "003_create_current_view.sql": ddl_current_view,
}
