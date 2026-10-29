"""The acceptance gate.

Extraction is non-deterministic, so acceptance must not be.  The model proposes;
a JSON schema and the rules below dispose.  Nothing reaches the store unvalidated.

These are not confidence thresholds.  A confidence threshold asks the model how
sure it is, which is asking the thing under test to grade itself.  Every rule
here is a fact that can be checked against the document or against arithmetic:
the cited section either occurs in the source or it does not.

A record failing any rule is dead-lettered with the rule's name and the
offending value -- not dropped, because the failures are the dataset the next
extractor version is improved against.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Callable, Iterable, Sequence

Record = dict[str, object]

_FIELD_PATH_RE = re.compile(r"^covenants\.[A-Za-z0-9_]+\.[a-z0-9_]+$")
_RATIO_RE = re.compile(r"^\d+\.\d{2} to \d+\.\d{2}$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DIRECTIONS = {"min", "max"}


@dataclass(frozen=True)
class Context:
    """Everything a rule is allowed to consult. No network, no model."""

    text: str
    definitions: frozenset[str]


@dataclass(frozen=True)
class Violation:
    invariant: str
    field_path: str | None
    reason: str
    #: When true, every field of the covenant is rejected, not just this one.
    covenant_scope: bool = False


def covenant_of(field_path: str) -> str | None:
    parts = field_path.split(".")
    return parts[1] if len(parts) >= 3 else None


def attr_of(field_path: str) -> str | None:
    parts = field_path.split(".")
    return parts[-1] if len(parts) >= 3 else None


def _s(record: Record, key: str) -> str:
    value = record.get(key)
    return value if isinstance(value, str) else ""


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------
def field_path_well_formed(records: Sequence[Record], ctx: Context) -> list[Violation]:
    out = []
    for r in records:
        path = _s(r, "field_path")
        if not _FIELD_PATH_RE.match(path):
            out.append(Violation(
                "field_path_well_formed", path or None,
                f"field_path {path!r} is not covenants.<id>.<attr>",
            ))
    return out


def citation_resolves(records: Sequence[Record], ctx: Context) -> list[Violation]:
    """The cited section must occur verbatim in the normalised source.

    This is the rule that catches a confident hallucination, because a model
    that invents a value usually invents a plausible section number to go with
    it, and a plausible section number is not a present one.
    """
    out = []
    for r in records:
        citation = _s(r, "citation_section")
        if citation and citation not in ctx.text:
            out.append(Violation(
                "citation_resolves", _s(r, "field_path"),
                f"cited {citation!r} does not occur in the source text",
            ))
    return out


def ratio_threshold_format(records: Sequence[Record], ctx: Context) -> list[Violation]:
    """A ratio threshold is canonicalised to `N.NN to 1.00`.

    Agreements write the same ratio as "4.00:1.00", "4.00 to 1.00" and
    "4.0 to 1.0".  Downstream comparison across a portfolio needs one form.
    """
    out = []
    for r in records:
        path = _s(r, "field_path")
        if attr_of(path) != "threshold":
            continue
        value = _s(r, "value")
        looks_like_ratio = ":" in value or re.search(r"\bto\b", value, re.IGNORECASE)
        if looks_like_ratio and not _RATIO_RE.match(value):
            out.append(Violation(
                "ratio_threshold_format", path,
                f"threshold {value!r} is not canonical 'N.NN to 1.00'",
            ))
    return out


def direction_in_enum(records: Sequence[Record], ctx: Context) -> list[Violation]:
    out = []
    for r in records:
        path = _s(r, "field_path")
        if attr_of(path) != "direction":
            continue
        value = _s(r, "value")
        if value not in _DIRECTIONS:
            out.append(Violation(
                "direction_in_enum", path,
                f"direction {value!r} not in {sorted(_DIRECTIONS)}",
            ))
    return out


def maintenance_has_frequency(records: Sequence[Record], ctx: Context) -> list[Violation]:
    """A maintenance covenant is tested on a schedule, so it must say how often.

    Maintenance versus incurrence is a difference in trigger, not in kind: the
    same debt limit is a maintenance test if it is retested every quarter and an
    incurrence test if it is checked only when the borrower acts.  A maintenance
    covenant with no frequency has lost the thing that made it maintenance.
    """
    by_covenant: dict[str, set[str]] = {}
    types: dict[str, str] = {}
    for r in records:
        path = _s(r, "field_path")
        cov, attr = covenant_of(path), attr_of(path)
        if not cov or not attr:
            continue
        by_covenant.setdefault(cov, set()).add(attr)
        if attr == "type":
            types[cov] = _s(r, "value").strip().lower()

    out = []
    for cov, kind in sorted(types.items()):
        if kind == "maintenance" and "test_frequency" not in by_covenant.get(cov, set()):
            out.append(Violation(
                "maintenance_has_frequency", f"covenants.{cov}.type",
                f"covenant {cov} is maintenance but carries no test_frequency",
                covenant_scope=True,
            ))
    return out


def step_down_dates_ordered(records: Sequence[Record], ctx: Context) -> list[Violation]:
    """Step-down dates ascend and never repeat.

    A step-down schedule that is out of order, or that names the same date
    twice, means two different thresholds are in force at once -- which makes
    the covenant untestable rather than merely wrong.
    """
    out = []
    for r in records:
        path = _s(r, "field_path")
        if attr_of(path) != "step_down_dates":
            continue
        raw = _s(r, "value")
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        malformed = [p for p in parts if not _ISO_DATE_RE.match(p)]
        if malformed:
            out.append(Violation(
                "step_down_dates_ordered", path,
                f"step-down dates {malformed} are not ISO YYYY-MM-DD",
            ))
            continue
        try:
            parsed = [date.fromisoformat(p) for p in parts]
        except ValueError as exc:
            out.append(Violation("step_down_dates_ordered", path, f"unparseable date: {exc}"))
            continue
        if any(b <= a for a, b in zip(parsed, parsed[1:])):
            out.append(Violation(
                "step_down_dates_ordered", path,
                f"step-down dates {parts} are not strictly ascending",
            ))
    return out


def defined_term_resolves(records: Sequence[Record], ctx: Context) -> list[Violation]:
    """A referenced defined term must exist in this document's definitions.

    This is the hypothesis `covenant-definition-graph` left behind: extraction
    fails on definition closure rather than on numbers.  The eval's failure
    taxonomy counts how often this rule is the one that fires.
    """
    out = []
    for r in records:
        path = _s(r, "field_path")
        attr = attr_of(path) or ""
        if attr != "defined_term" and not attr.endswith("_term"):
            continue
        # Whitespace-normalised on both sides: the definitions index is keyed
        # that way because terms wrap across lines in the source.
        term = " ".join(_s(r, "value").strip().strip('"').split())
        if term and term not in ctx.definitions:
            out.append(Violation(
                "defined_term_resolves", path,
                f"defined term {term!r} is not defined in this agreement",
            ))
    return out


InvariantFn = Callable[[Sequence[Record], Context], list[Violation]]

INVARIANTS: tuple[InvariantFn, ...] = (
    field_path_well_formed,
    citation_resolves,
    ratio_threshold_format,
    direction_in_enum,
    maintenance_has_frequency,
    step_down_dates_ordered,
    defined_term_resolves,
)

INVARIANT_NAMES: tuple[str, ...] = tuple(fn.__name__ for fn in INVARIANTS)


def check(records: Sequence[Record], ctx: Context) -> list[Violation]:
    """Run every rule. Rules are independent, so all failures are reported."""
    out: list[Violation] = []
    for fn in INVARIANTS:
        out.extend(fn(records, ctx))
    return out


@dataclass(frozen=True)
class GateResult:
    accepted: list[Record]
    rejected: list[tuple[Record, Violation]]
    violations: list[Violation]


def gate(records: Sequence[Record], ctx: Context) -> GateResult:
    """Partition records into what may be stored and what must be dead-lettered.

    A covenant-scoped violation rejects every field of that covenant, because a
    maintenance covenant missing its frequency is not three good fields and one
    bad one -- the covenant as a whole cannot be evaluated.
    """
    violations = check(records, ctx)

    bad_paths: dict[str, Violation] = {}
    bad_covenants: dict[str, Violation] = {}
    for v in violations:
        if v.covenant_scope and v.field_path:
            cov = covenant_of(v.field_path)
            if cov:
                bad_covenants.setdefault(cov, v)
        elif v.field_path:
            bad_paths.setdefault(v.field_path, v)

    accepted: list[Record] = []
    rejected: list[tuple[Record, Violation]] = []
    for r in records:
        path = _s(r, "field_path")
        cov = covenant_of(path)
        if path in bad_paths:
            rejected.append((r, bad_paths[path]))
        elif cov and cov in bad_covenants:
            rejected.append((r, bad_covenants[cov]))
        else:
            accepted.append(r)
    return GateResult(accepted=accepted, rejected=rejected, violations=violations)
