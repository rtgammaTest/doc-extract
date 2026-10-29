"""Fetch, segment, extract, validate, write.

The order matters and the shape is the argument: the model sits in the middle
of the pipeline, not at the end of it. Everything after step 4 is deterministic,
so the same model output always produces the same stored rows, and the question
"why does the store contain this" always has an answer that does not involve
asking a model again.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from jsonschema import Draft202012Validator

from . import invariants as inv
from . import prompt as prompt_mod
from . import segment as seg
from .config import Config
from .schema import extraction_response_schema
from .store import Row, utc_now


@dataclass
class Result:
    doc_id: str
    accepted: int = 0
    rejected: int = 0
    sections: list[str] = field(default_factory=list)
    definitions: list[str] = field(default_factory=list)
    violations: list[inv.Violation] = field(default_factory=list)
    prompt_sha: str = ""
    model: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "sections": self.sections,
            "definitions": self.definitions,
            "model": self.model,
            "prompt_sha": self.prompt_sha,
            "violations": [
                {"invariant": v.invariant, "field_path": v.field_path, "reason": v.reason}
                for v in self.violations
            ],
        }


def _provenance(config: Config, model_name: str, sha: str, when: str) -> dict[str, Any]:
    return {
        "extractor_version": config.extractor_version,
        "model": model_name,
        "prompt_sha": sha,
        "extracted_at": when,
    }


def _citation_offset(text: str, citation: Any) -> int | None:
    if not isinstance(citation, str) or not citation:
        return None
    idx = text.find(citation)
    return idx if idx >= 0 else None


def run(
    doc_id: str,
    raw_text: str,
    *,
    model,
    store,
    config: Config,
    section_numbers: Sequence[str] | None = None,
) -> Result:
    text = seg.normalise(raw_text)
    parcel = seg.segment(doc_id, raw_text, list(section_numbers) if section_numbers else None)
    definitions = frozenset(seg.find_definitions(text))

    sha = prompt_mod.prompt_sha(config.prompt_version)
    rendered = prompt_mod.render(parcel.text, config.prompt_version)
    schema = extraction_response_schema()

    result = Result(
        doc_id=doc_id,
        sections=parcel.sections,
        definitions=parcel.definitions,
        prompt_sha=sha,
        model=getattr(model, "name", "unknown"),
    )
    when = utc_now()
    provenance = _provenance(config, result.model, sha, when)

    response = model.generate(rendered, schema)

    # 4a. The shape of the response, before anything in it is believed.
    errors = sorted(Draft202012Validator(schema).iter_errors(response), key=lambda e: e.path)
    if errors:
        reason = "; ".join(f"{list(e.path)}: {e.message}" for e in errors[:5])
        store.write_dead_letter([{
            "doc_id": doc_id,
            "field_path": None,
            "invariant": "response_schema",
            "reason": reason,
            "raw_response": _truncate(response),
            "rejected_at": when,
            **{k: v for k, v in provenance.items() if k != "extracted_at"},
        }])
        result.rejected = 1
        result.violations = [inv.Violation("response_schema", None, reason)]
        return result

    records: list[dict[str, Any]] = list(response.get("fields", []))
    for record in records:
        record["citation_offset"] = _citation_offset(text, record.get("citation_section"))

    # 4b. The content of the response, checked against the document itself.
    ctx = inv.Context(text=text, definitions=definitions)
    verdict = inv.gate(records, ctx)

    accepted_rows: list[Row] = [
        {
            "doc_id": doc_id,
            "field_path": r.get("field_path"),
            "value": r.get("value"),
            "citation_section": r.get("citation_section"),
            "citation_page": r.get("citation_page"),
            "citation_offset": r.get("citation_offset"),
            "confidence": r.get("confidence"),
            **provenance,
        }
        for r in verdict.accepted
    ]

    rejected_rows: list[Row] = [
        {
            "doc_id": doc_id,
            "field_path": r.get("field_path"),
            "invariant": v.invariant,
            "reason": v.reason,
            "raw_response": _truncate(r),
            "rejected_at": when,
            **{k: val for k, val in provenance.items() if k != "extracted_at"},
        }
        for r, v in verdict.rejected
    ]

    store.write_extractions(accepted_rows)
    store.write_dead_letter(rejected_rows)

    result.accepted = len(accepted_rows)
    result.rejected = len(rejected_rows)
    result.violations = verdict.violations
    return result


def _truncate(payload: Any, limit: int = 8000) -> str:
    import json  # noqa: PLC0415

    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return blob if len(blob) <= limit else blob[:limit] + "...[truncated]"
