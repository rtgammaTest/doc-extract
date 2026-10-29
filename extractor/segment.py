"""Turn a credit agreement into the few thousand tokens worth sending.

Sending a whole agreement to a model is expensive and makes the answer worse:
the covenant you want is three paragraphs, and the definitions it depends on
are forty pages away.  So segmentation does two things -- find the covenant
sections, then follow the defined terms they reference and pull those in too.

The second part is the one that matters.  `covenant-definition-graph` found
that extraction fails on definition closure rather than on numbers, and you
cannot close definitions you did not send.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# The source HTML arrives with its curly quotes already destroyed -- every
# defined-term delimiter comes through as U+FFFD.  Defined terms are quote
# delimited, so without this the term lookup below finds nothing at all.
_REPLACEMENTS = (
    ("‘", "'"), ("’", "'"),
    ("“", '"'), ("”", '"'),
    ("–", "-"), ("—", "-"),
    ("�", '"'),
    ("§", "Section "),
    (" ", " "),
)

_SECTION_RE = re.compile(r"\bSection\s+(\d+(?:\.\d+)*)", re.IGNORECASE)

# Defined terms wrap across lines constantly in real agreements -- a term is
# quoted, not line-bounded -- so these allow newlines inside the quotes and the
# captured term is whitespace-normalised before it is used as a key.
_DEFINITION_RE = re.compile(r'"([A-Z][^"]{2,120}?)"\s+(?:shall\s+)?means?\b')
_QUOTED_TERM_RE = re.compile(r'"([A-Z][^"]{2,120}?)"')


def normalise_term(term: str) -> str:
    return " ".join(term.split())


def normalise(text: str) -> str:
    """Canonical text form. Every offset in the store is an offset into this."""
    for bad, good in _REPLACEMENTS:
        text = text.replace(bad, good)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


@dataclass(frozen=True)
class Definition:
    term: str
    start: int
    end: int

    @property
    def text(self) -> str:  # pragma: no cover - convenience only
        return f"{self.term} [{self.start}:{self.end}]"


@dataclass(frozen=True)
class Section:
    number: str
    start: int
    end: int


def find_definitions(text: str) -> dict[str, Definition]:
    """Defined terms, keyed by term.

    A definition is a quoted capitalised phrase followed by "means" or "shall
    mean".  Its body runs to the next definition or to a blank line, whichever
    comes first -- crude, but it keeps whole-document context out of the prompt.
    """
    starts = [(m.start(), normalise_term(m.group(1))) for m in _DEFINITION_RE.finditer(text)]
    out: dict[str, Definition] = {}
    for i, (start, term) in enumerate(starts):
        hard_stop = starts[i + 1][0] if i + 1 < len(starts) else len(text)
        para = text.find("\n\n", start)
        end = min(hard_stop, para if para != -1 else hard_stop)
        out[term] = Definition(term=term, start=start, end=end)
    return out


def find_sections(text: str) -> dict[str, Section]:
    """Sections, keyed by dotted number. A section runs to the next one."""
    hits = [(m.start(), m.group(1)) for m in _SECTION_RE.finditer(text)]
    # Keep the first mention of each number; later ones are cross-references.
    seen: dict[str, int] = {}
    for start, number in hits:
        seen.setdefault(number, start)
    ordered = sorted(seen.items(), key=lambda kv: kv[1])
    out: dict[str, Section] = {}
    for i, (number, start) in enumerate(ordered):
        end = ordered[i + 1][1] if i + 1 < len(ordered) else len(text)
        out[number] = Section(number=number, start=start, end=end)
    return out


def referenced_terms(fragment: str, definitions: dict[str, Definition]) -> set[str]:
    """Defined terms a fragment leans on."""
    found = (normalise_term(m.group(1)) for m in _QUOTED_TERM_RE.finditer(fragment))
    return {t for t in found if t in definitions}


def close_over_definitions(
    fragment: str,
    definitions: dict[str, Definition],
    text: str,
    depth: int = 2,
) -> list[Definition]:
    """Follow references transitively, because definitions cite definitions.

    Depth 2 is a deliberate cut, not a limitation of the code: in both of the
    agreements in the gold set the reference graph bottoms out by depth 3, and
    each extra level roughly doubles the prompt.
    """
    collected: dict[str, Definition] = {}
    frontier = referenced_terms(fragment, definitions)
    for _ in range(depth):
        if not frontier:
            break
        nxt: set[str] = set()
        for term in sorted(frontier):
            if term in collected:
                continue
            definition = definitions[term]
            collected[term] = definition
            body = text[definition.start:definition.end]
            nxt |= referenced_terms(body, definitions) - collected.keys()
        frontier = nxt
    return [collected[t] for t in sorted(collected)]


@dataclass(frozen=True)
class Segment:
    doc_id: str
    text: str
    sections: list[str]
    definitions: list[str]


def segment(
    doc_id: str,
    raw_text: str,
    section_numbers: list[str] | None = None,
    depth: int = 2,
) -> Segment:
    """Covenant sections plus the definitions they reference, as one prompt body."""
    text = normalise(raw_text)
    definitions = find_definitions(text)
    sections = find_sections(text)

    wanted = section_numbers or sorted(sections)
    chosen = [sections[n] for n in wanted if n in sections]

    body = "\n\n".join(text[s.start:s.end].strip() for s in chosen)
    needed = close_over_definitions(body, definitions, text, depth=depth)

    parts = ["### COVENANT SECTIONS", body]
    if needed:
        parts.append("### REFERENCED DEFINITIONS")
        parts.extend(text[d.start:d.end].strip() for d in needed)

    return Segment(
        doc_id=doc_id,
        text="\n\n".join(parts),
        sections=[s.number for s in chosen],
        definitions=[d.term for d in needed],
    )
