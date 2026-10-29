"""The model call, and a deterministic stand-in for it.

``FakeModel`` is not a mock that returns a canned blob. It is a naive regex
extractor over the same segmented text -- which makes it the baseline the real
model has to beat, and makes it useful twice: the test suite and the eval both
run end to end with no network, and ``make eval-baseline`` gives you the number
to put the model's accuracy next to.

That matters for the argument this repo exists to make. "An LLM extracts
covenants" is not interesting on its own; "an LLM beats a careful regex by N
points, and here is where each of them fails" is.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from . import segment as seg

_RATIO_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?::|\bto\b)\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
_SECTION_HEAD_RE = re.compile(r"\bSection\s+(\d+(?:\.\d+)*)", re.IGNORECASE)
# Whitespace-tolerant throughout: an agreement wraps "each fiscal\nquarter"
# wherever the margin falls, and a regex with a literal space silently misses it.
_MAINTENANCE_RE = re.compile(
    r"as\s+of\s+the\s+(?:last\s+day|end)\s+of\s+each\s+(?:fiscal\s+)?(quarter|month|year)",
    re.IGNORECASE,
)
_MIN_RE = re.compile(r"less\s+than|lower\s+than|at\s+least|fall\s+below", re.IGNORECASE)
_MAX_RE = re.compile(r"greater\s+than|more\s+than|exceeds?|at\s+most", re.IGNORECASE)


class Model(Protocol):
    name: str

    def generate(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]: ...


# --------------------------------------------------------------------------
# Deterministic baseline
# --------------------------------------------------------------------------
@dataclass
class FakeModel:
    """A careful regex, dressed as a model. No network, no cost, no variance."""

    name: str = "regex-baseline-1"
    fixture: Path | None = None

    def generate(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        if self.fixture is not None:
            return json.loads(self.fixture.read_text(encoding="utf-8"))

        body = prompt.split("### COVENANT SECTIONS", 1)[-1]
        body = body.split("### REFERENCED DEFINITIONS", 1)[0]

        heads = [(m.start(), m.group(1)) for m in _SECTION_HEAD_RE.finditer(body)]
        fields: list[dict[str, Any]] = []
        for i, (start, number) in enumerate(heads):
            end = heads[i + 1][0] if i + 1 < len(heads) else len(body)
            clause = body[start:end]
            ratio = _RATIO_RE.search(clause)
            if not ratio:
                continue

            cov = number.replace(".", "_")
            citation = f"Section {number}"

            # Written as it was found. Agreements use "4.00:1.00" and
            # "4.0 to 1.0" interchangeably, and the gate is what forces one
            # canonical form -- so the baseline deliberately does not guess.
            raw = ratio.group(0).strip()
            canonical = f"{float(ratio.group(1)):.2f} to {float(ratio.group(2)):.2f}"
            value = canonical if re.search(r"\bto\b", raw, re.IGNORECASE) else raw

            fields.append(self._field(f"covenants.{cov}.threshold", value, citation, 0.55))

            if _MIN_RE.search(clause):
                fields.append(self._field(f"covenants.{cov}.direction", "min", citation, 0.5))
            elif _MAX_RE.search(clause):
                fields.append(self._field(f"covenants.{cov}.direction", "max", citation, 0.5))

            maintenance = _MAINTENANCE_RE.search(clause)
            kind = "maintenance" if maintenance else "incurrence"
            fields.append(self._field(f"covenants.{cov}.type", kind, citation, 0.5))
            if maintenance:
                period = maintenance.group(1).lower()
                fields.append(self._field(
                    f"covenants.{cov}.test_frequency", f"each fiscal {period}", citation, 0.5,
                ))

        return {"fields": fields}

    @staticmethod
    def _field(path: str, value: str, citation: str, confidence: float) -> dict[str, Any]:
        return {
            "field_path": path,
            "value": value,
            "citation_section": citation,
            "confidence": confidence,
        }


# --------------------------------------------------------------------------
# Vertex AI
# --------------------------------------------------------------------------
def _openapi_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Both Gemini surfaces take an OpenAPI-flavoured schema that rejects a few
    JSON Schema keywords. Strip them rather than maintaining a second schema."""
    if isinstance(schema, dict):
        return {
            k: _openapi_schema(v)
            for k, v in schema.items()
            if k not in {"additionalProperties", "$schema"}
        }
    if isinstance(schema, list):
        return [_openapi_schema(v) for v in schema]
    return schema


# Kept for readers coming from the Vertex docs, which call it this.
_vertex_schema = _openapi_schema


@dataclass
class VertexModel:
    """Gemini with structured output. Imported lazily so the rest runs without it."""

    name: str
    project: str
    #: A Vertex location, not the deployment region. See Config.vertex_location.
    location: str
    temperature: float = 0.0
    max_retries: int = 2

    def generate(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        import vertexai  # noqa: PLC0415
        from vertexai.generative_models import GenerationConfig, GenerativeModel  # noqa: PLC0415

        vertexai.init(project=self.project, location=self.location)
        model = GenerativeModel(self.name)
        config = GenerationConfig(
            temperature=self.temperature,
            response_mime_type="application/json",
            response_schema=_openapi_schema(schema),
        )

        last: Exception | None = None
        for _ in range(self.max_retries + 1):
            try:
                response = model.generate_content(prompt, generation_config=config)
                return json.loads(response.text)
            except (json.JSONDecodeError, ValueError) as exc:
                # Retry only malformed output. Anything else -- quota, auth,
                # a 5xx -- is not made better by sending the document again.
                last = exc
        raise RuntimeError(f"model returned unparseable JSON {self.max_retries + 1} times") from last


# --------------------------------------------------------------------------
# Gemini API (AI Studio)
# --------------------------------------------------------------------------
@dataclass
class GeminiApiModel:
    """Gemini through generativelanguage.googleapis.com, keyed by an API key.

    A separate entitlement path from Vertex: it needs no project IAM, no service
    account and no publisher-model access, which makes it the way through when a
    project can reach Vertex the service but not Vertex the models.

    stdlib urllib rather than requests, so the container gains no dependency.
    """

    name: str
    api_key: str
    temperature: float = 0.0
    max_retries: int = 2
    endpoint: str = "https://generativelanguage.googleapis.com/v1beta"

    def payload(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        return {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": self.temperature,
                "responseMimeType": "application/json",
                "responseSchema": _openapi_schema(schema),
            },
        }

    def generate(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        import urllib.error  # noqa: PLC0415
        import urllib.request  # noqa: PLC0415

        if not self.api_key:
            raise RuntimeError("MODEL_BACKEND=gemini-api needs GEMINI_API_KEY")

        url = f"{self.endpoint}/models/{self.name}:generateContent"
        body = json.dumps(self.payload(prompt, schema)).encode("utf-8")

        last: Exception | None = None
        for _ in range(self.max_retries + 1):
            request = urllib.request.Request(
                url, data=body, method="POST",
                headers={
                    "Content-Type": "application/json",
                    # Header rather than a query parameter, so the key does not
                    # end up in a URL that gets logged.
                    "x-goog-api-key": self.api_key,
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    envelope = json.loads(response.read().decode("utf-8"))
                return json.loads(_first_text(envelope))
            except urllib.error.HTTPError as exc:
                # urllib's HTTPError stringifies as "HTTP Error 404: Not Found"
                # and silently discards the body -- which is the only part that
                # says WHICH model or key was wrong. Read it and re-raise with
                # the detail attached; an error that does not name its cause
                # costs more than the request did.
                try:
                    detail = exc.read().decode("utf-8", "replace")[:2000]
                except Exception:
                    detail = "<body unavailable>"
                raise RuntimeError(
                    f"gemini api {exc.code} calling {self.name}: {detail}"
                ) from exc
            except (json.JSONDecodeError, KeyError, IndexError) as exc:
                # Retry only malformed output. Quota, auth and 5xx are not made
                # better by sending the document again.
                last = exc
        raise RuntimeError(f"model returned unparseable JSON {self.max_retries + 1} times") from last


def _first_text(envelope: dict[str, Any]) -> str:
    """Pull the one text part out of a generateContent response."""
    if "error" in envelope:
        raise RuntimeError(f"gemini api error: {envelope['error']}")
    return envelope["candidates"][0]["content"]["parts"][0]["text"]


def build_model(config) -> Model:
    if config.model_backend == "gemini-api":
        return GeminiApiModel(
            name=config.model_id,
            api_key=config.gemini_api_key,
            temperature=config.temperature,
            max_retries=config.max_retries,
        )
    if config.model_backend == "vertex":
        return VertexModel(
            name=config.model_id,
            project=config.project,
            location=config.vertex_location,
            temperature=config.temperature,
            max_retries=config.max_retries,
        )
    if config.model_backend == "fake":
        return FakeModel()
    # Loudly, rather than falling back to the baseline. A silent fallback writes
    # rows stamped with the extractor_version you asked for and values the
    # regex produced -- the run looks successful and the comparison is against
    # itself. Better to refuse to start.
    raise ValueError(
        f"unknown MODEL_BACKEND {config.model_backend!r}; "
        f"expected one of: fake, vertex, gemini-api"
    )
