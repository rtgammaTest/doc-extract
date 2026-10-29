"""Settings, all from the environment.

Two of these decide whether the service talks to Google at all. ``MODEL_BACKEND``
and ``STORE_BACKEND`` both default to local stubs, so the pipeline runs, the
tests pass and the eval scores without a GCP project, an API key or a network.
That is deliberate: a reviewer who cannot run it has to take your word for it.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Config:
    project: str = ""
    region: str = "us-east4"
    #: Where Gemini is called. Deliberately separate from `region`: Gemini is
    #: served from a subset of regions, and us-east4 is not one of them, so
    #: reusing the deployment region gives a 404 on the publisher model.
    vertex_location: str = "us-central1"
    dataset: str = "doc_extract"
    raw_bucket: str = ""

    extractor_version: str = "1.0.0"
    #: gemini-2.5-flash is deprecated for new accounts and returns a bare 404
    #: that says nothing about deprecation. Pin a current model.
    model_id: str = "gemini-3.8-flash"

    #: "vertex" calls Vertex AI, "gemini-api" calls the AI Studio endpoint
    #: with an API key, "fake" uses the regex baseline. Default "fake".
    model_backend: str = "fake"
    #: Only read when model_backend is "gemini-api".
    gemini_api_key: str = ""
    #: "bigquery" writes to BigQuery; "jsonl" writes files. Default "jsonl".
    store_backend: str = "jsonl"

    out_dir: Path = ROOT / "out"
    prompt_version: str = "v1"

    #: Two, not five. The failure mode that costs real money is a retry loop
    #: sending whole documents; a low cap is cheaper than a budget alert.
    max_retries: int = 2
    temperature: float = 0.0

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            project=_env("PROJECT", ""),
            region=_env("REGION", "us-east4"),
            vertex_location=_env("VERTEX_LOCATION", "us-central1"),
            dataset=_env("DATASET", "doc_extract"),
            raw_bucket=_env("RAW_BUCKET", ""),
            extractor_version=_env("EXTRACTOR_VERSION", "1.0.0"),
            model_id=_env("MODEL_ID", "gemini-3.8-flash"),
            model_backend=_env("MODEL_BACKEND", "fake").lower(),
            gemini_api_key=_env("GEMINI_API_KEY", ""),
            store_backend=_env("STORE_BACKEND", "jsonl").lower(),
            out_dir=Path(_env("OUT_DIR", str(ROOT / "out"))),
            prompt_version=_env("PROMPT_VERSION", "v1"),
            max_retries=int(_env("MAX_RETRIES", "2")),
            temperature=float(_env("TEMPERATURE", "0.0")),
        )
