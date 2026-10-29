"""HTTP shim for the Eventarc push subscription.

Deliberately thin. Everything this file does is turn a CloudEvent into a
(doc_id, text) pair and hand it to ``pipeline.run`` -- which is why the pipeline
is tested and this is not. Logic that lives in a request handler can only be
exercised by making requests.
"""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI, Request, Response

from .config import Config
from .model import build_model
from .pipeline import run
from .store import build_store

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("doc-extract")

app = FastAPI(title="doc-extract", version=os.environ.get("EXTRACTOR_VERSION", "1.0.0"))

CONFIG = Config.from_env()
MODEL = build_model(CONFIG)
STORE = build_store(CONFIG)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {
        "status": "ok",
        "extractor_version": CONFIG.extractor_version,
        "model_backend": CONFIG.model_backend,
        "store_backend": CONFIG.store_backend,
    }


def _object_ref(payload: dict, headers) -> tuple[str, str] | None:
    """Accept both CloudEvent delivery modes rather than guessing at deploy time."""
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    bucket = data.get("bucket") or headers.get("ce-bucket")
    name = data.get("name") or data.get("objectId")
    if bucket and name:
        return str(bucket), str(name)
    return None


def _read_object(bucket: str, name: str) -> str:
    from google.cloud import storage  # noqa: PLC0415

    client = storage.Client(project=CONFIG.project or None)
    return client.bucket(bucket).blob(name).download_as_text()


@app.post("/")
async def handle(request: Request) -> Response:
    try:
        payload = await request.json()
    except ValueError:
        payload = {}

    ref = _object_ref(payload, request.headers)
    if ref is None:
        # 204, not 4xx: a malformed event retried forever is a billing problem,
        # and there is nothing a retry would fix.
        log.warning("event carried no object reference; dropping")
        return Response(status_code=204)

    bucket, name = ref
    doc_id = f"gs://{bucket}/{name}"
    log.info("extracting %s", doc_id)

    try:
        text = _read_object(bucket, name)
    except Exception:
        log.exception("could not read %s", doc_id)
        # 500 so Eventarc retries: this one may be transient.
        return Response(status_code=500)

    result = run(doc_id, text, model=MODEL, store=STORE, config=CONFIG)
    log.info(
        "%s accepted=%d rejected=%d sections=%d definitions=%d",
        doc_id, result.accepted, result.rejected,
        len(result.sections), len(result.definitions),
    )
    return Response(status_code=204)
