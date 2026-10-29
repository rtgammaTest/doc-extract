"""End to end, with no network and no project.

The pipeline is deterministic after the model call, so these assert the thing
that actually matters operationally: the same input produces the same stored
rows, re-running appends rather than overwrites, and nothing reaches the store
without provenance.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from extractor import segment as seg
from extractor.config import Config
from extractor.model import FakeModel
from extractor.pipeline import run
from extractor.store import JsonlStore

FIXTURE = Path(__file__).resolve().parent.parent / "eval" / "fixtures" / "sample_agreement.txt"
SECTIONS = ["9.1", "9.2", "9.3"]


def _run(tmp: Path, version: str = "1.0.0"):
    config = Config(extractor_version=version, out_dir=tmp)
    store = JsonlStore(out_dir=tmp)
    result = run(
        "sample_agreement", FIXTURE.read_text(encoding="utf-8"),
        model=FakeModel(), store=store, config=config, section_numbers=SECTIONS,
    )
    return result, store


def test_pipeline_accepts_and_rejects_as_expected():
    with tempfile.TemporaryDirectory() as tmp:
        result, store = _run(Path(tmp))
        assert result.accepted == 10
        assert result.rejected == 1
        assert len(store.read_extractions()) == 10
        assert len(store.read_dead_letter()) == 1


def test_the_rejected_row_is_the_uncanonical_ratio():
    with tempfile.TemporaryDirectory() as tmp:
        _, store = _run(Path(tmp))
        dead = store.read_dead_letter()[0]
        assert dead["invariant"] == "ratio_threshold_format"
        assert dead["field_path"] == "covenants.9_2.threshold"
        assert "2.50:1.00" in dead["reason"]


def test_rejections_are_kept_not_dropped():
    """The failures are the dataset the next extractor version improves against."""
    with tempfile.TemporaryDirectory() as tmp:
        _, store = _run(Path(tmp))
        dead = store.read_dead_letter()[0]
        assert dead["raw_response"], "a rejection with no payload cannot be replayed"
        assert dead["prompt_sha"] and dead["model"] and dead["extractor_version"]


def test_every_stored_row_carries_provenance():
    with tempfile.TemporaryDirectory() as tmp:
        _, store = _run(Path(tmp))
        for row in store.read_extractions():
            for key in ("extractor_version", "model", "prompt_sha", "extracted_at", "doc_id"):
                assert row.get(key), f"{row['field_path']} is missing {key}"


def test_citation_offsets_point_at_the_real_text():
    with tempfile.TemporaryDirectory() as tmp:
        _, store = _run(Path(tmp))
        text = seg.normalise(FIXTURE.read_text(encoding="utf-8"))
        for row in store.read_extractions():
            offset, citation = row["citation_offset"], row["citation_section"]
            assert offset is not None
            assert text[offset:offset + len(citation)] == citation


def test_rerun_appends_a_new_version_and_keeps_the_old_one():
    """Nothing is updated in place. That is the whole point of the store."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _run(tmp_path, version="1.0.0")
        _run(tmp_path, version="1.1.0")

        rows = JsonlStore(out_dir=tmp_path).read_extractions()
        assert len(rows) == 20, "the first run's rows must survive the second"
        assert {r["extractor_version"] for r in rows} == {"1.0.0", "1.1.0"}


def test_current_returns_one_row_per_field_and_prefers_the_latest():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _run(tmp_path, version="1.0.0")
        _run(tmp_path, version="1.1.0")

        current = JsonlStore(out_dir=tmp_path).current()
        assert len(current) == 10
        assert {r["extractor_version"] for r in current} == {"1.1.0"}
        assert len({(r["doc_id"], r["field_path"]) for r in current}) == 10


def test_pipeline_is_deterministic():
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        _, store_a = _run(Path(a))
        _, store_b = _run(Path(b))
        key = lambda rows: sorted(  # noqa: E731
            (r["field_path"], r["value"], r["citation_offset"]) for r in rows
        )
        assert key(store_a.read_extractions()) == key(store_b.read_extractions())


def test_malformed_model_output_is_dead_lettered_whole():
    class Broken:
        name = "broken-1"

        def generate(self, prompt, schema):
            return {"fields": [{"value": "4.00 to 1.00"}]}  # no field_path

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        store = JsonlStore(out_dir=tmp_path)
        result = run(
            "sample_agreement", FIXTURE.read_text(encoding="utf-8"),
            model=Broken(), store=store, config=Config(out_dir=tmp_path),
            section_numbers=SECTIONS,
        )
        assert result.accepted == 0
        assert store.read_extractions() == []
        assert store.read_dead_letter()[0]["invariant"] == "response_schema"


def test_segmentation_pulls_in_referenced_definitions():
    with tempfile.TemporaryDirectory() as tmp:
        result, _ = _run(Path(tmp))
        assert "Consolidated Leverage Ratio" in result.definitions
        assert "Consolidated Interest Coverage Ratio" in result.definitions
