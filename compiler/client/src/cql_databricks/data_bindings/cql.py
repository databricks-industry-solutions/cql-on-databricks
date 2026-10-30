"""CONTENT-RESOLVER — the few facts read off CQL text: header identity, includes, value sets, retrieves.

Moved unchanged from the retired ``cql_databricks_knowledge.knowledge_query`` and
``scripts/knowledge_sync.py`` (``KNOWLEDGE-LAKE-DIRECT``: the derivations relocate, they do not change).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

_HEADER_RE = re.compile(r"library\s+(\S+)\s+version\s+'([^']*)'")
_INCLUDE_RE = re.compile(
    r"""\binclude\s+(?:"([^"]+)"|([A-Za-z_][\w.]*))\s+version\s+'([^']*)'"""
    r"""(?:\s+called\s+(?:"([^"]+)"|([A-Za-z_]\w*)))?""")
_VALUESET_RE = re.compile(r"""\bvalueset\s+"[^"]*"\s*:\s*'([^']*)'""")
_VALUESET_DECL_RE = re.compile(r"""\bvalueset\s+"([^"]*)"\s*:\s*'([^']*)'""")
_RETRIEVE_RE = re.compile(r"\[\s*([A-Z][A-Za-z0-9]+)\b")
_USING_RE = re.compile(r"\busing\s+([A-Za-z_.]+)\s+version\s+'([^']*)'")


@dataclass
class IncludeRef:
    """A declared dependency edge parsed from an ``include`` directive."""
    name: str
    version: str
    alias: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name, "version": self.version}
        if self.alias is not None:
            d["alias"] = self.alias
        return d


def _dedupe(xs: list[str]) -> list[str]:
    return list(dict.fromkeys(xs))


def parse_identity(cql: str, *, url: str, version: str) -> tuple[str, str]:
    """The CQL-header ``(name, version)`` — what include resolution resolves against.

    It can diverge from the FHIR ``Library.version`` (FHIR "2026.0.0" vs CQL "5.0.000"), so the two are
    kept apart. Falls back to the canonical URL's last segment and the FHIR version when the CQL has no
    parseable header, so a library with CQL always has an identity.
    """
    header = _HEADER_RE.search(cql or "")
    if header:
        return header.group(1), header.group(2)
    return url.rstrip("/").rsplit("/", 1)[-1], version


def parse_includes(cql: str) -> list[IncludeRef]:
    """Declared ``include`` edges (name+version, optional alias) in source order."""
    out: list[IncludeRef] = []
    for m in _INCLUDE_RE.finditer(cql):
        ref = IncludeRef(name=(m.group(1) or m.group(2) or ""), version=(m.group(3) or ""))
        alias = m.group(4) or m.group(5)
        if alias:
            ref.alias = alias
        out.append(ref)
    return out


def parse_value_sets(cql: str) -> list[str]:
    return _dedupe([m.group(1) for m in _VALUESET_RE.finditer(cql) if m.group(1)])


def parse_value_set_declarations(cql: str) -> list[tuple[str, str]]:
    """AGENT-VALUE-SET-LOOKUP — each ``valueset "Name": 'url'`` declaration, as ``(name, url)``.

    The name is the author's: the terminology layout carries no value-set title, so a declared name is
    the one human label a value set has."""
    return _dedupe([(m.group(1), m.group(2)) for m in _VALUESET_DECL_RE.finditer(cql) if m.group(2)])


def parse_resource_types(cql: str) -> list[str]:
    return _dedupe([m.group(1) for m in _RETRIEVE_RE.finditer(cql) if m.group(1)])


def parse_model(cql: str) -> str:
    """The data model a library declares with ``using`` (e.g. ``FHIR``), or ''."""
    using = _USING_RE.search(cql or "")
    return using.group(1) if using else ""


def _num(seg: str | None) -> float:
    if seg is None:
        return math.nan
    if seg == "":
        return 0.0
    try:
        return float(seg)
    except ValueError:
        return math.nan


def compare_versions(a: str, b: str) -> int:
    """Compare two version strings numerically per dotted segment, lexically as tiebreak."""
    pa, pb = a.split("."), b.split(".")
    for i in range(max(len(pa), len(pb))):
        sa = pa[i] if i < len(pa) else None
        sb = pb[i] if i < len(pb) else None
        na, nb = _num(sa), _num(sb)
        if math.isfinite(na) and math.isfinite(nb) and na != nb:
            return -1 if na < nb else 1
        if not math.isfinite(na) or not math.isfinite(nb):
            c = (sa or "") > (sb or "") or -((sa or "") < (sb or ""))
            if c != 0:
                return c
    return 0
