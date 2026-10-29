"""The prompt is an input to the system, so it is versioned like one.

Every stored row carries ``prompt_sha``, the sha256 of the exact template that
produced it.  Change a word and the hash changes, which means you can always
answer the question an auditor actually asks: not "what does the system say
now" but "what produced this row, and would it still".
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


@lru_cache(maxsize=8)
def load_template(version: str = "v1") -> str:
    path = PROMPTS_DIR / f"{version}.txt"
    if not path.exists():
        raise FileNotFoundError(f"no prompt template {version!r} in {PROMPTS_DIR}")
    return path.read_text(encoding="utf-8")


def prompt_sha(version: str = "v1") -> str:
    return hashlib.sha256(load_template(version).encode("utf-8")).hexdigest()


def render(document_text: str, version: str = "v1") -> str:
    return load_template(version).replace("{{DOCUMENT}}", document_text)
