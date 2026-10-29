"""The two Gemini surfaces, exercised without calling either.

Both take an OpenAPI-flavoured schema that rejects JSON Schema keywords the
gate relies on, so the stripping is tested rather than assumed -- a schema that
is silently rejected gives you a model with no structure at all, which looks
like a bad prompt rather than a bad request.
"""
from __future__ import annotations

import json

from extractor.config import Config
from extractor.model import (FakeModel, GeminiApiModel, VertexModel, _first_text,
                             _openapi_schema, build_model)
from extractor.schema import extraction_response_schema


def test_openapi_schema_strips_rejected_keywords():
    stripped = _openapi_schema(extraction_response_schema())
    blob = json.dumps(stripped)
    assert "additionalProperties" not in blob
    assert "$schema" not in blob


def test_openapi_schema_keeps_the_parts_that_matter():
    stripped = _openapi_schema(extraction_response_schema())
    item = stripped["properties"]["fields"]["items"]
    assert set(item["required"]) == {"field_path", "value", "citation_section"}
    assert item["properties"]["confidence"]["maximum"] == 1


def test_gemini_payload_shape():
    model = GeminiApiModel(name="gemini-2.5-flash", api_key="test-key")
    payload = model.payload("PROMPT", extraction_response_schema())
    assert payload["contents"][0]["parts"][0]["text"] == "PROMPT"
    config = payload["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["temperature"] == 0.0
    assert "responseSchema" in config


def test_gemini_api_key_never_reaches_the_payload():
    """It travels in a header, so it cannot end up in a logged URL or body."""
    model = GeminiApiModel(name="gemini-2.5-flash", api_key="super-secret")
    assert "super-secret" not in json.dumps(model.payload("p", {"type": "object"}))


def test_first_text_unwraps_a_response():
    envelope = {"candidates": [{"content": {"parts": [{"text": '{"fields": []}'}]}}]}
    assert json.loads(_first_text(envelope)) == {"fields": []}


def test_first_text_raises_on_an_error_envelope():
    try:
        _first_text({"error": {"code": 404, "message": "nope"}})
    except RuntimeError as exc:
        assert "404" in str(exc)
    else:
        raise AssertionError("an error envelope must not be parsed as a result")


def test_missing_api_key_fails_before_the_network():
    model = GeminiApiModel(name="gemini-2.5-flash", api_key="")
    try:
        model.generate("p", {"type": "object"})
    except RuntimeError as exc:
        assert "GEMINI_API_KEY" in str(exc)
    else:
        raise AssertionError("an empty key must fail loudly, not silently")


def test_build_model_selects_the_backend():
    assert isinstance(build_model(Config(model_backend="fake")), FakeModel)
    assert isinstance(build_model(Config(model_backend="vertex")), VertexModel)
    assert isinstance(
        build_model(Config(model_backend="gemini-api", gemini_api_key="k")), GeminiApiModel
    )


def test_unknown_backend_raises_instead_of_falling_back():
    """A silent fallback is worse than a crash.

    Falling back to the regex writes rows stamped with whatever
    extractor_version was requested and values the baseline produced. The run
    looks successful, the eval compares the baseline against itself, and the
    only thing that gives it away is the model column.
    """
    for typo in ("gemni", "gemini", "vertexai", ""):
        try:
            build_model(Config(model_backend=typo))
        except ValueError as exc:
            assert "MODEL_BACKEND" in str(exc)
        else:
            raise AssertionError(f"{typo!r} should not have produced a model")


def test_vertex_location_is_not_the_deployment_region():
    model = build_model(Config(model_backend="vertex", region="us-east4",
                               vertex_location="us-central1"))
    assert model.location == "us-central1"


def test_http_error_body_reaches_the_message():
    """urllib discards the response body, which is the only part that says why.

    A 404 from the Gemini API means "wrong model name" and the body says which
    one. Without this, the operator sees "HTTP Error 404: Not Found" and has to
    go hunting for information the failed call already had.
    """
    import io
    import json as _json
    import urllib.error
    from unittest import mock

    body = _json.dumps({"error": {"code": 404, "message": "models/bogus is not found"}}).encode()
    err = urllib.error.HTTPError("http://x", 404, "Not Found", {}, io.BytesIO(body))

    with mock.patch("urllib.request.urlopen", side_effect=err):
        try:
            GeminiApiModel(name="bogus", api_key="k").generate("p", {"type": "object"})
        except RuntimeError as exc:
            assert "404" in str(exc)
            assert "bogus" in str(exc)
            assert "is not found" in str(exc), "the body must survive into the message"
        else:
            raise AssertionError("an HTTP error must not be swallowed")
