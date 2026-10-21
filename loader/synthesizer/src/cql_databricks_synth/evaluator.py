"""Concrete three-valued ELM evaluation over a generated patient bundle (SYNTH-PDS-ORACLE).

One semantic core serves two duties: computing the expected-results ground truth for a
solved skeleton (before any engine executes), and the post-fill verification pass that
re-checks every emitted patient (SYNTH-PDS-VERIFY). The evaluator interprets the measure
closure's OWN ELM — multi-library, user-defined functions included — and raises
``UnsupportedElm`` on anything outside its vocabulary (SYNTH-PDS-IR-CONSUMER); it never
guesses semantics. FHIRHelpers is the one library modeled natively (coercions only).

Scope notes:
- Generated data is always full-precision; precision-qualified comparisons (``day of``)
  truncate both operands. Date->DateTime conversion pins midnight (CQL's uncertainty
  model is not implemented) — the solver keeps solved points >= 1 day away from every
  boundary, so the pin cannot flip a generated patient's result.
- ``Now()``/``Today()`` are pinned to the end of the root Measurement Period, so ground
  truth is a function of (measure, config, seed) alone (SYNTH-PDS-DETERMINISM).
- Retrieves filter by type + code filter; QI-Core profile templateIds do not filter
  (matching the engine's type-level mounts). ``union`` de-duplicates structurally.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from typing import Any

from . import SynthesizerError, UnsupportedElm
from .library import Code, Env, Lib
from .valuesets import ValueSetIndex

FHIR_NS = "{http://hl7.org/fhir}"
_MS = dt.timedelta(milliseconds=1)

_CHOICE_SUFFIXES = (
    "DateTime", "Period", "Date", "Instant", "Boolean", "Integer", "Decimal",
    "Quantity", "Range", "Ratio", "String", "Time", "CodeableConcept", "Age", "Reference",
    "Code", "Coding",  # extension value[x] variants
)

#: Component count of a truncation key per precision.
_PRECISION_PARTS = {"Year": 1, "Month": 2, "Week": 3, "Day": 3,
                    "Hour": 4, "Minute": 5, "Second": 6, "Millisecond": 7}


@dataclass(frozen=True)
class Quantity:
    value: float
    unit: str = "1"


@dataclass(frozen=True)
class Concept:
    codes: tuple[Code, ...]


@dataclass(frozen=True)
class CqlInterval:
    """A CQL interval with boundary closure.

    A CLOSED null boundary is interpreted as the point type's minimum/maximum (per the
    CQL spec) — ``Interval[onset, null]`` extends to the end of time. An OPEN null
    boundary is genuinely unknown and stays null.
    """
    low: Any
    high: Any
    low_closed: bool = True
    high_closed: bool = True

    def _extreme(self, *, high: bool):
        sibling = self.low if high else self.high
        if isinstance(sibling, dt.datetime) or sibling is None:
            return dt.datetime(9999, 12, 31, 23, 59, 59, 999000) if high else dt.datetime(1, 1, 1)
        if isinstance(sibling, dt.date):
            return dt.date(9999, 12, 31) if high else dt.date(1, 1, 1)
        if isinstance(sibling, int):
            return 2**31 - 1 if high else -(2**31)
        raise UnsupportedElm(f"null boundary beside {type(sibling).__name__}")

    def effective_low(self):
        if self.low is None:
            return self._extreme(high=False) if self.low_closed else None
        return self.low if self.low_closed else _step(self.low, +1)

    def effective_high(self):
        if self.high is None:
            return self._extreme(high=True) if self.high_closed else None
        return self.high if self.high_closed else _step(self.high, -1)


def _step(point: Any, direction: int):
    if isinstance(point, dt.datetime):
        return point + direction * _MS
    if isinstance(point, dt.date):
        return point + dt.timedelta(days=direction)
    if isinstance(point, int):
        return point + direction
    if isinstance(point, float):
        return point + direction * 1e-8
    raise UnsupportedElm(f"no point successor for {type(point).__name__}")


def parse_temporal(text: str):
    if "T" in text:
        value = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        if value.tzinfo is not None:
            value = value.astimezone(dt.timezone.utc).replace(tzinfo=None)
        return value
    return dt.date.fromisoformat(text)


def _maybe_parse(value: Any):
    """Parse JSON scalars/elements into CQL values on property access."""
    if isinstance(value, str) and value[:4].isdigit() and len(value) >= 10 and value[4] == "-":
        try:
            return parse_temporal(value)
        except ValueError:
            return value
    if isinstance(value, dict) and "value" in value and (
            "unit" in value or ("code" in value and "system" in value and "coding" not in value)):
        try:
            return Quantity(value=float(value["value"]),
                            unit=value.get("code") or value.get("unit") or "1")
        except (TypeError, ValueError):
            return value
    return value


def _key(value: Any) -> str:
    """A structural identity for distinct/union semantics."""
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True, default=str)
    return repr(value)


def _distinct(items: list) -> list:
    seen: set[str] = set()
    out = []
    for item in items:
        k = _key(item)
        if k not in seen:
            seen.add(k)
            out.append(item)
    return out


def _prec_key(value: Any, precision: str | None):
    """A comparison key truncated to a precision (full-precision inputs only)."""
    if isinstance(value, dt.datetime):
        parts = (value.year, value.month, value.day, value.hour, value.minute,
                 value.second, value.microsecond)
    elif isinstance(value, dt.date):
        parts = (value.year, value.month, value.day, 0, 0, 0, 0)
    elif isinstance(value, (int, float)):
        return value
    else:
        raise UnsupportedElm(f"precision comparison over {type(value).__name__}")
    n = _PRECISION_PARTS.get(precision or "Millisecond")
    if n is None:
        raise UnsupportedElm(f"precision {precision!r}")
    return parts[:n]


@dataclass
class Bundle:
    """One patient's resources, grouped by FHIR resource type."""
    by_type: dict[str, list[dict]] = field(default_factory=dict)

    def add(self, resource: dict) -> None:
        self.by_type.setdefault(resource["resourceType"], []).append(resource)

    def of_type(self, resource_type: str) -> list[dict]:
        return list(self.by_type.get(resource_type, []))


class Evaluator:
    """Evaluates a closure's defines against one Bundle under CQL three-valued logic."""

    def __init__(self, env: Env | dict, valuesets: ValueSetIndex, bundle: Bundle,
                 parameters: dict[str, Any] | None = None):
        self.env = env if isinstance(env, Env) else Env([env])
        self.valuesets = valuesets
        self.bundle = bundle
        # LANG-PARAMETER: run-time parameter values, bound by name in every library of the
        # closure. A supplied value wins over the declared default (CQL spec 5.2, "Parameters").
        self.parameters = parameters or {}
        self._lib: Lib = self.env.lib(self.env.root)
        self._frames: list[dict[str, Any]] = []
        self._memo: dict[tuple[str, str], Any] = {}
        self._now: dt.datetime | None = None

    # -- public surface -------------------------------------------------------------

    def define(self, name: str, library: str | None = None):
        lib = self.env.lib(library) if library else self.env.lib(self.env.root)
        key = (lib.name, name)
        if key not in self._memo:
            node = lib.defines.get(name)
            if node is None:
                raise SynthesizerError(f"define {name!r} not in library {lib.name}")
            saved_lib, saved_frames = self._lib, self._frames
            self._lib, self._frames = lib, []
            try:
                self._memo[key] = self.eval(node["expression"])
            finally:
                self._lib, self._frames = saved_lib, saved_frames
        return self._memo[key]

    def define_names(self, library: str | None = None) -> list[str]:
        return list(self.env.lib(library or self.env.root).defines)

    def parameter(self, name: str, lib: Lib | None = None):
        lib = lib or self._lib
        node = lib.parameters.get(name)
        if node is None:
            raise SynthesizerError(f"parameter {name!r} not declared in {lib.name}")
        if name in self.parameters:
            return self.parameters[name]
        default = node.get("default")
        if default is None:
            # Parameters bind globally by name at run time; an included library's
            # undefaulted parameter takes the root library's bound value.
            root = self.env.lib(self.env.root)
            root_node = root.parameters.get(name) if root is not lib else None
            if root_node is None or root_node.get("default") is None:
                raise UnsupportedElm(
                    f"parameter {lib.name}.{name!r} has no default and no run-time value "
                    f"(pass --measurement-period)")
            lib, node, default = root, root_node, root_node["default"]
        saved = self._lib
        self._lib = lib
        try:
            return self.eval(default)
        finally:
            self._lib = saved

    def valueset_url(self, name: str, lib: Lib | None = None) -> str:
        lib = lib or self._lib
        url = lib.valuesets.get(name)
        if url is None:
            raise SynthesizerError(f"value set {name!r} not declared in {lib.name}")
        return url

    def eval_in(self, library: str, node: dict):
        """Evaluate a node in a named library's context (the solver's const-eval hook)."""
        saved_lib, saved_frames = self._lib, self._frames
        self._lib, self._frames = self.env.lib(library), []
        try:
            return self.eval(node)
        finally:
            self._lib, self._frames = saved_lib, saved_frames

    def now(self) -> dt.datetime:
        """Pinned evaluation timestamp: the end of the root Measurement Period."""
        if self._now is None:
            saved = self._lib
            self._lib = self.env.lib(self.env.root)
            try:
                period = self.parameter("Measurement Period")
            finally:
                self._lib = saved
            if not isinstance(period, CqlInterval):
                raise UnsupportedElm("Measurement Period is not an interval; cannot pin Now()")
            self._now = period.effective_high()
        return self._now

    # -- dispatch --------------------------------------------------------------------

    def eval(self, node: dict):
        kind = node.get("type")
        handler = getattr(self, f"_eval_{kind}", None)
        if handler is None:
            raise UnsupportedElm(
                f"ELM node type {kind!r} (library {self._lib.name}, locator "
                f"{node.get('locator')}) is outside the synthesizer's vocabulary")
        return handler(node)

    def _operands(self, node: dict) -> list:
        ops = node["operand"]
        if isinstance(ops, dict):
            return [self.eval(ops)]
        return [self.eval(op) for op in ops]

    # -- literals & structure ----------------------------------------------------------

    def _eval_Literal(self, node: dict):
        value_type = node["valueType"].split("}")[-1]
        raw = node["value"]
        if value_type in ("Integer", "Long"):
            return int(raw)
        if value_type == "Decimal":
            return float(raw)
        if value_type == "Boolean":
            return raw in (True, "true")
        if value_type == "String":
            return raw
        raise UnsupportedElm(f"Literal of type {value_type}")

    def _eval_Null(self, node: dict):
        return None

    def _eval_Quantity(self, node: dict):
        return Quantity(value=float(node["value"]), unit=node.get("unit", "1"))

    def _eval_Interval(self, node: dict):
        def closed(flag_key: str, expr_key: str) -> bool:
            if node.get(expr_key) is not None:
                value = self.eval(node[expr_key])
                return bool(value) if value is not None else True
            return node.get(flag_key, True)

        return CqlInterval(
            low=self.eval(node["low"]) if node.get("low") else None,
            high=self.eval(node["high"]) if node.get("high") else None,
            low_closed=closed("lowClosed", "lowClosedExpression"),
            high_closed=closed("highClosed", "highClosedExpression"))

    def _eval_List(self, node: dict):
        return [self.eval(e) for e in node.get("element", [])]

    def _eval_Tuple(self, node: dict):
        return {e["name"]: self.eval(e["value"]) for e in node.get("element", [])}

    def _eval_Instance(self, node: dict):
        return {e["name"]: self.eval(e["value"]) for e in node.get("element", [])}

    def _int_part(self, node: dict, part, default: int | None) -> int:
        if part is None:
            if default is None:
                raise UnsupportedElm(f"partial-precision {node['type']}")
            return default
        value = self.eval(part)
        if not isinstance(value, int):
            raise UnsupportedElm(f"non-literal {node['type']} component")
        return value

    def _eval_DateTime(self, node: dict):
        offset = node.get("timezoneOffset")
        value = dt.datetime(
            self._int_part(node, node.get("year"), None),
            self._int_part(node, node.get("month"), 1),
            self._int_part(node, node.get("day"), 1),
            self._int_part(node, node.get("hour"), 0),
            self._int_part(node, node.get("minute"), 0),
            self._int_part(node, node.get("second"), 0),
            self._int_part(node, node.get("millisecond"), 0) * 1000)
        if offset is not None:
            hours = self.eval(offset) if isinstance(offset, dict) else offset
            value -= dt.timedelta(hours=float(hours))
        return value

    def _eval_Date(self, node: dict):
        return dt.date(self._int_part(node, node.get("year"), None),
                       self._int_part(node, node.get("month"), 1),
                       self._int_part(node, node.get("day"), 1))

    def _eval_MaxValue(self, node: dict):
        t = node["valueType"].split("}")[-1]
        if t == "DateTime":
            return dt.datetime(9999, 12, 31, 23, 59, 59, 999000)
        if t == "Date":
            return dt.date(9999, 12, 31)
        raise UnsupportedElm(f"MaxValue of {t}")

    def _eval_MinValue(self, node: dict):
        t = node["valueType"].split("}")[-1]
        if t == "DateTime":
            return dt.datetime(1, 1, 1)
        if t == "Date":
            return dt.date(1, 1, 1)
        raise UnsupportedElm(f"MinValue of {t}")

    def _eval_Now(self, node: dict):
        return self.now()

    def _eval_Today(self, node: dict):
        now = self.now()
        return now.date() if isinstance(now, dt.datetime) else now

    # -- references --------------------------------------------------------------------

    def _eval_ParameterRef(self, node: dict):
        lib = self.env.resolve(self._lib, node.get("libraryName"))
        return self.parameter(node["name"], lib)

    def _eval_ExpressionRef(self, node: dict):
        lib = self.env.resolve(self._lib, node.get("libraryName"))
        return self.define(node["name"], lib.name)

    def _eval_AliasRef(self, node: dict):
        return self._lookup(node["name"])

    def _eval_OperandRef(self, node: dict):
        return self._lookup(node["name"])

    def _eval_QueryLetRef(self, node: dict):
        return self._lookup(node["name"])

    def _eval_IdentifierRef(self, node: dict):
        # Sort-clause identifiers resolve against the element being sorted ($this frame).
        this = self._lookup("$this")
        return self._segment(this, node["name"])

    def _lookup(self, name: str):
        for frame in reversed(self._frames):
            if name in frame:
                return frame[name]
        raise SynthesizerError(f"name {name!r} not in scope (library {self._lib.name})")

    def _eval_CodeRef(self, node: dict):
        lib = self.env.resolve(self._lib, node.get("libraryName"))
        code = lib.codes.get(node["name"])
        if code is None:
            raise SynthesizerError(f"code {node['name']!r} not in {lib.name}")
        return code

    # -- function calls -----------------------------------------------------------------

    def _eval_FunctionRef(self, node: dict):
        alias = node.get("libraryName")
        if alias and self._lib.includes.get(alias, alias) == "FHIRHelpers":
            return self._fhirhelpers(node["name"], self._operands(node))
        lib = self.env.resolve(self._lib, alias)
        overloads = lib.functions.get(node["name"], [])
        args = self._operands(node)
        matches = [f for f in overloads if len(f.get("operand", [])) == len(args)]
        if not matches:
            raise UnsupportedElm(f"function {lib.name}.{node['name']}/{len(args)} not found")
        if len(matches) > 1:
            matches = self._match_overload(matches, args, lib, node)
        fn = matches[0]
        if fn.get("external"):
            raise UnsupportedElm(f"external function {lib.name}.{node['name']}")
        frame = {op["name"]: arg for op, arg in zip(fn["operand"], args)}
        saved_lib, saved_frames = self._lib, self._frames
        self._lib, self._frames = lib, [frame]
        try:
            return self.eval(fn["expression"])
        finally:
            self._lib, self._frames = saved_lib, saved_frames

    @staticmethod
    def _match_overload(matches: list[dict], args: list, lib: Lib, node: dict) -> list[dict]:
        """Same-arity overloads: pick by rough runtime type of the first argument."""
        def spec_name(op: dict) -> str:
            spec = op.get("operandTypeSpecifier") or {}
            return (spec.get("name") or spec.get("type") or "").split("}")[-1]

        arg = args[0]
        want = ("Interval" if isinstance(arg, CqlInterval) else
                "DateTime" if isinstance(arg, dt.datetime) else
                "Date" if isinstance(arg, dt.date) else
                "Quantity" if isinstance(arg, Quantity) else
                "String" if isinstance(arg, str) else "")
        by_type = [f for f in matches if want and want in spec_name(f["operand"][0])]
        if len(by_type) == 1:
            return by_type
        choicy = [f for f in matches if spec_name(f["operand"][0]) in ("", "ChoiceTypeSpecifier")]
        if len(choicy) == 1:
            return choicy
        raise UnsupportedElm(
            f"ambiguous overload {lib.name}.{node['name']} for {type(args[0]).__name__}")

    def _fhirhelpers(self, name: str, args: list):
        value = args[0] if args else None
        if value is None:
            return None
        if name == "ToInterval":
            if isinstance(value, CqlInterval):
                return value
            if isinstance(value, dict):
                low = parse_temporal(value["start"]) if value.get("start") else None
                high = parse_temporal(value["end"]) if value.get("end") else None
                return CqlInterval(low=low, high=high)
            if isinstance(value, Quantity):
                return CqlInterval(low=value, high=value)
            raise UnsupportedElm(f"FHIRHelpers.ToInterval over {type(value).__name__}")
        if name == "ToValue":
            # The FHIR choice value coerced to its CQL type, per FHIRHelpers.ToValue.
            if isinstance(value, dict):
                if "coding" in value:
                    return self._fhirhelpers("ToConcept", [value])
                if "start" in value or "end" in value:
                    return self._fhirhelpers("ToInterval", [value])
            return _maybe_parse(value)
        if name == "ToQuantity":
            if isinstance(value, Quantity):
                return value
            if isinstance(value, dict):
                if "value" not in value and "coding" in value:
                    # A choice element read under the Quantity variant while the stored
                    # variant is a CodeableConcept (profile-only retrieves are
                    # type-level mounts, so out-of-profile resources DO reach here):
                    # the mismatched read is null, exactly like the engine's cast.
                    return None
                return Quantity(float(value["value"]),
                                value.get("code") or value.get("unit") or "1")
            raise UnsupportedElm(f"FHIRHelpers.ToQuantity over {type(value).__name__}")
        if name == "ToConcept":
            if isinstance(value, Concept):
                return value
            if isinstance(value, dict):
                return Concept(tuple(
                    Code(system=c.get("system", ""), code=c.get("code", ""),
                         display=c.get("display"))
                    for c in value.get("coding", [])))
            if isinstance(value, Quantity):
                # A choice element read under the concept variant while the stored
                # variant is a Quantity (type-level mounts let out-of-profile
                # resources reach here): the mismatched read is null.
                return None
            raise UnsupportedElm(f"FHIRHelpers.ToConcept over {type(value).__name__}")
        if name == "ToCode":
            if isinstance(value, dict):
                return Code(system=value.get("system", ""), code=value.get("code", ""),
                            display=value.get("display"))
            return value if isinstance(value, Code) else None
        if name == "ToString":
            if isinstance(value, str):
                return value
            if isinstance(value, (int, float, bool)):
                return str(value)
            if isinstance(value, (dt.date, dt.datetime)):
                return value.isoformat()
            raise UnsupportedElm(f"FHIRHelpers.ToString over {type(value).__name__}")
        if name == "ToDateTime":
            return self._to_datetime(value)
        if name == "ToDate":
            if isinstance(value, dt.datetime):
                return value.date()
            if isinstance(value, dt.date):
                return value
            if isinstance(value, str):
                parsed = parse_temporal(value)
                return parsed.date() if isinstance(parsed, dt.datetime) else parsed
        if name == "ToBoolean":
            return value if isinstance(value, bool) else None
        if name == "ToInteger":
            return int(value)
        if name == "ToDecimal":
            return float(value)
        if name == "ToTime":
            raise UnsupportedElm("FHIRHelpers.ToTime")
        raise UnsupportedElm(f"FHIRHelpers.{name}")

    @staticmethod
    def _to_datetime(value):
        if isinstance(value, dt.datetime):
            return value
        if isinstance(value, dt.date):
            # Date->DateTime pins midnight (documented; solver margins absorb it).
            return dt.datetime(value.year, value.month, value.day)
        if isinstance(value, str):
            return Evaluator._to_datetime(parse_temporal(value))
        raise UnsupportedElm(f"ToDateTime over {type(value).__name__}")

    # System conversion operators (the translator also emits these as node types).
    def _eval_ToDateTime(self, node: dict):
        value = self.eval(node["operand"])
        return None if value is None else self._to_datetime(value)

    def _eval_ToDate(self, node: dict):
        value = self.eval(node["operand"])
        return None if value is None else self._fhirhelpers("ToDate", [value])

    def _eval_ToDecimal(self, node: dict):
        value = self.eval(node["operand"])
        return None if value is None else float(value)

    def _eval_ToInteger(self, node: dict):
        value = self.eval(node["operand"])
        return None if value is None else int(value)

    def _eval_ToString(self, node: dict):
        value = self.eval(node["operand"])
        return None if value is None else self._fhirhelpers("ToString", [value])

    def _eval_ToQuantity(self, node: dict):
        value = self.eval(node["operand"])
        return None if value is None else self._fhirhelpers("ToQuantity", [value])

    def _eval_ToConcept(self, node: dict):
        value = self.eval(node["operand"])
        if value is None:
            return None
        if isinstance(value, Code):
            return Concept((value,))
        return self._fhirhelpers("ToConcept", [value])

    def _eval_ToList(self, node: dict):
        value = self.eval(node["operand"])
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    # -- retrieve & query ----------------------------------------------------------------

    def _eval_Retrieve(self, node: dict):
        data_type = node["dataType"]
        if not data_type.startswith(FHIR_NS):
            raise UnsupportedElm(f"Retrieve of non-FHIR type {data_type}")
        resources = self.bundle.of_type(data_type.removeprefix(FHIR_NS))
        codes = node.get("codes")
        if codes is None:
            return resources
        code_property = node.get("codeProperty")
        if not code_property:
            raise UnsupportedElm("Retrieve with codes but no codeProperty")
        if codes.get("type") == "ValueSetRef":
            lib = self.env.resolve(self._lib, codes.get("libraryName"))
            url = self.valueset_url(codes["name"], lib)
            return [r for r in resources
                    if self._concepts_match(
                        r.get(code_property),
                        lambda s, c: self.valuesets.contains(url, s, c))]
        direct = self.eval(codes)
        wanted = {(c.system, c.code) for c in (direct if isinstance(direct, list) else [direct])
                  if isinstance(c, Code)}
        if not wanted:
            raise UnsupportedElm(f"Retrieve codes filter of type {codes.get('type')}")
        return [r for r in resources
                if self._concepts_match(r.get(code_property), lambda s, c: (s, c) in wanted)]

    @staticmethod
    def _concepts_match(element: Any, member) -> bool:
        concepts = element if isinstance(element, list) else [element]
        for concept in concepts:
            if not isinstance(concept, dict):
                continue
            for coding in concept.get("coding", []):
                if member(coding.get("system", ""), coding.get("code", "")):
                    return True
        return False

    def _eval_Query(self, node: dict):
        sources = node.get("source", [])
        if len(sources) != 1:
            raise UnsupportedElm("multi-source Query")
        if node.get("aggregate"):
            raise UnsupportedElm("Query with aggregate clause")
        alias = sources[0]["alias"]
        items = self.eval(sources[0]["expression"])
        if items is None:
            return None
        # A query over a single object (not a list) is SINGULAR: it yields one value or
        # null, not a list (CQL query semantics).
        singular = not isinstance(items, list)
        if singular:
            items = [items]

        lets = node.get("let") or []
        relationships = node.get("relationship") or []
        where = node.get("where")
        ret = node.get("return")
        out = []
        for item in items:
            frame: dict[str, Any] = {alias: item}
            self._frames.append(frame)
            try:
                for let in lets:
                    frame[let["identifier"]] = self.eval(let["expression"])
                keep = True
                for rel in relationships:
                    related = self.eval(rel["expression"]) or []
                    hit = False
                    for other in related:
                        self._frames.append({rel["alias"]: other})
                        try:
                            if self.eval(rel["suchThat"]) is True:
                                hit = True
                                break
                        finally:
                            self._frames.pop()
                    kind = rel.get("type")
                    if kind == "With":
                        keep = hit
                    elif kind == "Without":
                        keep = not hit
                    else:
                        raise UnsupportedElm(f"query relationship {kind}")
                    if not keep:
                        break
                if keep and where is not None:
                    keep = self.eval(where) is True
                if keep:
                    out.append(self.eval(ret["expression"]) if ret else item)
            finally:
                self._frames.pop()
        if singular:
            return out[0] if out else None
        if ret is not None and ret.get("distinct", True):
            out = _distinct(out)
        return self._sorted(out, node.get("sort"))

    def _sorted(self, items: list, sort: dict | None) -> list:
        if not sort:
            return items
        by = sort.get("by", [])
        if len(by) != 1:
            raise UnsupportedElm("multi-key sort")
        spec = by[0]
        kind = spec.get("type")
        descending = spec.get("direction", "asc").startswith("desc")

        def sort_value(item):
            if kind == "ByDirection":
                return item
            if kind == "ByColumn":
                return self._segment(item, spec["path"])
            if kind == "ByExpression":
                self._frames.append({"$this": item})
                try:
                    return self.eval(spec["expression"])
                finally:
                    self._frames.pop()
            raise UnsupportedElm(f"sort {kind}")

        def sort_key(item):
            value = sort_value(item)
            if isinstance(value, Quantity):
                value = value.value
            return (value is not None, value)  # CQL: nulls sort first ascending

        return sorted(items, key=sort_key, reverse=descending)

    def _eval_SingletonFrom(self, node: dict):
        items = self.eval(node["operand"])
        if items is None or not items:
            return None
        if len(items) > 1:
            raise SynthesizerError("SingletonFrom over a list with more than one element")
        return items[0]

    def _eval_Exists(self, node: dict):
        items = self.eval(node["operand"])
        if items is None:
            return False
        return any(item is not None for item in items)

    def _eval_First(self, node: dict):
        items = self.eval(node["source"])
        return items[0] if items else None

    def _eval_Last(self, node: dict):
        items = self.eval(node["source"])
        return items[-1] if items else None

    def _eval_Flatten(self, node: dict):
        items = self.eval(node["operand"])
        if items is None:
            return None
        return [x for sub in items for x in (sub if isinstance(sub, list) else [sub])]

    def _eval_Distinct(self, node: dict):
        items = self.eval(node["operand"])
        return None if items is None else _distinct(items)

    def _eval_Union(self, node: dict):
        left, right = self._operands(node)
        if isinstance(left, CqlInterval) or isinstance(right, CqlInterval):
            raise UnsupportedElm("interval Union")
        return _distinct((left or []) + (right or []))

    def _eval_Intersect(self, node: dict):
        left, right = self._operands(node)
        if left is None or right is None:
            return None
        keys = {_key(x) for x in right}
        return [x for x in left if _key(x) in keys]

    def _eval_Except(self, node: dict):
        left, right = self._operands(node)
        if left is None:
            return None
        keys = {_key(x) for x in (right or [])}
        return [x for x in left if _key(x) not in keys]

    def _eval_Count(self, node: dict):
        items = self.eval(node["source"])
        return sum(1 for x in (items or []) if x is not None)

    def _eval_Sum(self, node: dict):
        items = [x for x in (self.eval(node["source"]) or []) if x is not None]
        if not items:
            return None
        if all(isinstance(x, Quantity) for x in items):
            unit = items[0].unit
            if any(q.unit != unit for q in items):
                raise UnsupportedElm("Sum over mixed quantity units")
            return Quantity(sum(q.value for q in items), unit)
        return sum(items)

    def _eval_Max(self, node: dict):
        items = [x for x in (self.eval(node["source"]) or []) if x is not None]
        return max(items) if items else None

    def _eval_Min(self, node: dict):
        items = [x for x in (self.eval(node["source"]) or []) if x is not None]
        return min(items) if items else None

    # -- properties ----------------------------------------------------------------------

    def _eval_Property(self, node: dict):
        if node.get("scope"):
            base = self._lookup(node["scope"])
        else:
            base = self.eval(node["source"])
        for segment in node["path"].split("."):
            base = self._segment(base, segment)
            if base is None:
                return None
        return base

    def _segment(self, base: Any, name: str):
        if base is None:
            return None
        if isinstance(base, Quantity):
            return {"value": base.value, "unit": base.unit}.get(name)
        if isinstance(base, Code):
            return {"system": base.system, "code": base.code, "display": base.display}.get(name)
        if isinstance(base, CqlInterval):
            return {"low": base.low, "high": base.high,
                    "start": base.low, "end": base.high}.get(name)
        if not isinstance(base, dict):
            if name == "value":
                return _maybe_parse(base)
            raise UnsupportedElm(f"property {name!r} on scalar {type(base).__name__}")
        if name in base:
            return _maybe_parse(base[name])
        for suffix in _CHOICE_SUFFIXES:
            key = f"{name}{suffix}"
            if key in base:
                return _maybe_parse(base[key])
        return None

    # -- logic (three-valued) ---------------------------------------------------------------

    def _eval_And(self, node: dict):
        left, right = self._operands(node)
        if left is False or right is False:
            return False
        if left is None or right is None:
            return None
        return True

    def _eval_Or(self, node: dict):
        left, right = self._operands(node)
        if left is True or right is True:
            return True
        if left is None or right is None:
            return None
        return False

    def _eval_Not(self, node: dict):
        value = self.eval(node["operand"])
        return None if value is None else not value

    def _eval_Implies(self, node: dict):
        left, right = self._operands(node)
        if left is False:
            return True
        if left is True:
            return right
        return True if right is True else None

    def _eval_IsNull(self, node: dict):
        return self.eval(node["operand"]) is None

    def _eval_IsTrue(self, node: dict):
        return self.eval(node["operand"]) is True

    def _eval_IsFalse(self, node: dict):
        return self.eval(node["operand"]) is False

    def _eval_If(self, node: dict):
        cond = self.eval(node["condition"])
        return self.eval(node["then"]) if cond is True else self.eval(node["else"])

    def _eval_Case(self, node: dict):
        comparand = self.eval(node["comparand"]) if node.get("comparand") else None
        for item in node.get("caseItem", []):
            when = self.eval(item["when"])
            if node.get("comparand"):
                hit = self._equal(comparand, when) is True
            else:
                hit = when is True
            if hit:
                return self.eval(item["then"])
        return self.eval(node["else"])

    def _eval_Coalesce(self, node: dict):
        operands = node["operand"]
        if isinstance(operands, dict):
            operands = [operands]
        if len(operands) == 1:
            items = self.eval(operands[0])
            if isinstance(items, list):
                return next((x for x in items if x is not None), None)
            return items
        for op in operands:
            value = self.eval(op)
            if value is not None:
                return value
        return None

    def _eval_Message(self, node: dict):
        return self.eval(node["source"])

    # -- type tests & casts ---------------------------------------------------------------

    _TYPE_CHECKS = {
        "DateTime": lambda v: isinstance(v, dt.datetime),
        "Date": lambda v: isinstance(v, dt.date) and not isinstance(v, dt.datetime),
        "Quantity": lambda v: isinstance(v, Quantity),
        "String": lambda v: isinstance(v, str),
        "Boolean": lambda v: isinstance(v, bool),
        "Integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "Decimal": lambda v: isinstance(v, float),
        "Code": lambda v: isinstance(v, Code),
        "Concept": lambda v: isinstance(v, Concept),
        "Period": lambda v: isinstance(v, dict) and ("start" in v or "end" in v),
        "Range": lambda v: isinstance(v, dict) and ("low" in v or "high" in v),
        "Ratio": lambda v: isinstance(v, dict) and "numerator" in v,
        "Age": lambda v: isinstance(v, Quantity),
        "Timing": lambda v: isinstance(v, dict) and ("repeat" in v or "event" in v),
        "Reference": lambda v: isinstance(v, dict) and "reference" in v,
        "CodeableConcept": lambda v: isinstance(v, dict) and "coding" in v,
        "instant": lambda v: isinstance(v, dt.datetime),
        "dateTime": lambda v: isinstance(v, dt.datetime),
        "date": lambda v: isinstance(v, dt.date) and not isinstance(v, dt.datetime),
        "string": lambda v: isinstance(v, str),
        "boolean": lambda v: isinstance(v, bool),
    }

    def _type_check(self, value, spec: dict) -> bool:
        kind = spec.get("type")
        if kind == "ChoiceTypeSpecifier":
            return any(self._type_check(value, c) for c in spec.get("choice", []))
        if kind == "IntervalTypeSpecifier":
            return isinstance(value, CqlInterval)
        if kind == "ListTypeSpecifier":
            return isinstance(value, list)
        name = (spec.get("name") or "").split("}")[-1]
        check = self._TYPE_CHECKS.get(name)
        if check is None:
            raise UnsupportedElm(f"type test against {name!r}")
        return check(value)

    def _eval_Is(self, node: dict):
        value = self.eval(node["operand"])
        if value is None:
            return False
        spec = node.get("isTypeSpecifier") or {"type": "NamedTypeSpecifier",
                                               "name": node.get("isType")}
        return self._type_check(value, spec)

    def _eval_As(self, node: dict):
        value = self.eval(node["operand"])
        if value is None:
            return None
        spec = node.get("asTypeSpecifier") or {"type": "NamedTypeSpecifier",
                                               "name": node.get("asType")}
        kind = spec.get("type")
        if kind == "IntervalTypeSpecifier":
            return value if isinstance(value, CqlInterval) else None
        if kind == "ListTypeSpecifier":
            return value if isinstance(value, list) else None
        if kind == "ChoiceTypeSpecifier":
            return value if any(self._safe_check(value, c) for c in spec.get("choice", [])) else None
        name = (spec.get("name") or "").split("}")[-1]
        check = self._TYPE_CHECKS.get(name)
        if check is not None:
            return value if check(value) else None
        # A FHIR complex type we do not model structurally: pass dicts through.
        return value if isinstance(value, dict) else None

    def _safe_check(self, value, spec) -> bool:
        try:
            return self._type_check(value, spec)
        except UnsupportedElm:
            return isinstance(value, dict)

    # -- equality & terminology --------------------------------------------------------------

    def _equal(self, left, right):
        if left is None or right is None:
            return None
        if isinstance(left, bool) or isinstance(right, bool):
            if isinstance(left, bool) and isinstance(right, bool):
                return left is right
            return None
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            return left == right
        if isinstance(left, Quantity) and isinstance(right, Quantity):
            if left.unit != right.unit and "1" not in (left.unit, right.unit):
                raise UnsupportedElm(f"Equal over units {left.unit!r}/{right.unit!r}")
            return left.value == right.value
        if isinstance(left, (dt.date, dt.datetime)) and isinstance(right, (dt.date, dt.datetime)):
            return self._cmp(left, right, None) == 0
        if type(left) is not type(right):
            return None
        return left == right

    def _eval_Equal(self, node: dict):
        left, right = self._operands(node)
        return self._equal(left, right)

    def _eval_NotEqual(self, node: dict):
        eq = self._eval_Equal(node)
        return None if eq is None else not eq

    def _equivalent(self, left, right) -> bool:
        if left is None and right is None:
            return True
        if left is None or right is None:
            return False
        left_codes = (left.codes if isinstance(left, Concept)
                      else (left,) if isinstance(left, Code) else None)
        right_codes = (right.codes if isinstance(right, Concept)
                       else (right,) if isinstance(right, Code) else None)
        if left_codes is not None and right_codes is not None:
            return any(a.system == b.system and a.code == b.code
                       for a in left_codes for b in right_codes)
        if isinstance(left, str) and isinstance(right, str):
            return " ".join(left.lower().split()) == " ".join(right.lower().split())
        return self._equal(left, right) is True

    def _eval_Equivalent(self, node: dict):
        left, right = self._operands(node)
        return self._equivalent(left, right)

    def _eval_InValueSet(self, node: dict):
        code = self.eval(node["code"])
        if code is None:
            return False
        vs = node["valueset"]
        lib = self.env.resolve(self._lib, vs.get("libraryName"))
        url = self.valueset_url(vs["name"], lib)
        if isinstance(code, str):
            return any(m.code == code for m in self.valuesets.members(url))
        codes = code.codes if isinstance(code, Concept) else (code,)
        return any(self.valuesets.contains(url, c.system, c.code) for c in codes)

    def _eval_AnyInValueSet(self, node: dict):
        codes = self.eval(node["codes"]) or []
        vs = node["valueset"]
        lib = self.env.resolve(self._lib, vs.get("libraryName"))
        url = self.valueset_url(vs["name"], lib)
        for item in codes:
            if item is None:
                continue
            members = item.codes if isinstance(item, Concept) else (item,)
            for c in members:
                if isinstance(c, Code) and self.valuesets.contains(url, c.system, c.code):
                    return True
        return False

    # -- comparison -----------------------------------------------------------------------------

    def _cmp(self, left, right, precision: str | None):
        """-1/0/1 comparison, or None for null; truncates temporals to ``precision``."""
        if left is None or right is None:
            return None
        if isinstance(left, Quantity) and isinstance(right, Quantity):
            if left.unit != right.unit and "1" not in (left.unit, right.unit):
                raise UnsupportedElm(f"comparison over units {left.unit!r}/{right.unit!r}")
            left, right = left.value, right.value
        if isinstance(left, (dt.date, dt.datetime)) or isinstance(right, (dt.date, dt.datetime)):
            lk, rk = _prec_key(left, precision), _prec_key(right, precision)
            if isinstance(lk, tuple) and isinstance(rk, tuple):
                n = min(len(lk), len(rk))
                lk, rk = lk[:n], rk[:n]
            return (lk > rk) - (lk < rk)
        if isinstance(left, str) and isinstance(right, str):
            return (left > right) - (left < right)
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            return (left > right) - (left < right)
        raise UnsupportedElm(f"comparison over {type(left).__name__}/{type(right).__name__}")

    def _compare(self, node: dict, ok) -> bool | None:
        left, right = self._operands(node)
        c = self._cmp(left, right, node.get("precision"))
        return None if c is None else ok(c)

    def _eval_Less(self, node: dict):
        return self._compare(node, lambda c: c < 0)

    def _eval_LessOrEqual(self, node: dict):
        return self._compare(node, lambda c: c <= 0)

    def _eval_Greater(self, node: dict):
        return self._compare(node, lambda c: c > 0)

    def _eval_GreaterOrEqual(self, node: dict):
        return self._compare(node, lambda c: c >= 0)

    @staticmethod
    def _to_point(value, *, low: bool):
        if isinstance(value, CqlInterval):
            return value.effective_low() if low else value.effective_high()
        return value

    def _eval_Before(self, node: dict):
        left, right = self._operands(node)
        a = self._to_point(left, low=False)
        b = self._to_point(right, low=True)
        c = self._cmp(a, b, node.get("precision"))
        return None if c is None else c < 0

    def _eval_After(self, node: dict):
        left, right = self._operands(node)
        a = self._to_point(left, low=True)
        b = self._to_point(right, low=False)
        c = self._cmp(a, b, node.get("precision"))
        return None if c is None else c > 0

    def _eval_SameAs(self, node: dict):
        left, right = self._operands(node)
        c = self._cmp(left, right, node.get("precision"))
        return None if c is None else c == 0

    def _eval_SameOrBefore(self, node: dict):
        left, right = self._operands(node)
        a = self._to_point(left, low=False)
        b = self._to_point(right, low=True)
        c = self._cmp(a, b, node.get("precision"))
        return None if c is None else c <= 0

    def _eval_SameOrAfter(self, node: dict):
        left, right = self._operands(node)
        a = self._to_point(left, low=True)
        b = self._to_point(right, low=False)
        c = self._cmp(a, b, node.get("precision"))
        return None if c is None else c >= 0

    def _contains(self, container, point, precision: str | None):
        if isinstance(container, list):
            if point is None:
                return None
            hits = [self._equal(point, item) for item in container]
            if any(h is True for h in hits):
                return True
            return None if any(h is None for h in hits) else False
        if point is None or container is None:
            return None
        if not isinstance(container, CqlInterval):
            raise UnsupportedElm(f"membership over {type(container).__name__}")
        low, high = container.effective_low(), container.effective_high()
        if low is None or high is None:
            return None
        low_ok = self._cmp(low, point, precision)
        high_ok = self._cmp(point, high, precision)
        if low_ok is None or high_ok is None:
            return None
        return low_ok <= 0 and high_ok <= 0

    def _eval_In(self, node: dict):
        point, container = self._operands(node)
        return self._contains(container, point, node.get("precision"))

    def _eval_Contains(self, node: dict):
        container, point = self._operands(node)
        return self._contains(container, point, node.get("precision"))

    def _eval_IncludedIn(self, node: dict):
        inner, outer = self._operands(node)
        if inner is None or outer is None:
            return None
        if not isinstance(inner, CqlInterval):
            return self._contains(outer, inner, node.get("precision"))
        if not isinstance(outer, CqlInterval):
            raise UnsupportedElm("IncludedIn over a non-interval container")
        bounds = (inner.effective_low(), inner.effective_high(),
                  outer.effective_low(), outer.effective_high())
        if any(b is None for b in bounds):
            return None
        precision = node.get("precision")
        return (self._cmp(bounds[0], bounds[2], precision) >= 0 and
                self._cmp(bounds[1], bounds[3], precision) <= 0)

    def _eval_Overlaps(self, node: dict):
        left, right = self._operands(node)
        if left is None or right is None:
            return None
        if not isinstance(left, CqlInterval) or not isinstance(right, CqlInterval):
            raise UnsupportedElm("Overlaps over non-intervals")
        bounds = (left.effective_low(), left.effective_high(),
                  right.effective_low(), right.effective_high())
        if any(b is None for b in bounds):
            return None
        precision = node.get("precision")
        return (self._cmp(bounds[0], bounds[3], precision) <= 0 and
                self._cmp(bounds[2], bounds[1], precision) <= 0)

    # -- temporal -----------------------------------------------------------------------------

    def _eval_CalculateAgeAt(self, node: dict):
        if node.get("precision") not in ("Year", "Month"):
            raise UnsupportedElm(f"CalculateAgeAt precision {node.get('precision')!r}")
        birth, as_of = self._operands(node)
        if birth is None or as_of is None:
            return None
        if isinstance(birth, dt.datetime):
            birth = birth.date()
        if isinstance(as_of, dt.datetime):
            as_of = as_of.date()
        months = (as_of.year - birth.year) * 12 + (as_of.month - birth.month)
        if as_of.day < birth.day:
            months -= 1
        return months // 12 if node["precision"] == "Year" else months

    def _eval_CalculateAge(self, node: dict):
        birth = self.eval(node["operand"])
        return self._age_at(birth, self._eval_Today({}), node.get("precision"))

    def _age_at(self, birth, as_of, precision):
        if birth is None or as_of is None:
            return None
        if isinstance(birth, dt.datetime):
            birth = birth.date()
        if isinstance(as_of, dt.datetime):
            as_of = as_of.date()
        months = (as_of.year - birth.year) * 12 + (as_of.month - birth.month)
        if as_of.day < birth.day:
            months -= 1
        if precision == "Year":
            return months // 12
        if precision == "Month":
            return months
        raise UnsupportedElm(f"CalculateAge precision {precision!r}")

    def _eval_DateFrom(self, node: dict):
        value = self.eval(node["operand"])
        if value is None:
            return None
        if isinstance(value, dt.datetime):
            return value.date()
        if isinstance(value, dt.date):
            return value
        raise UnsupportedElm("DateFrom over a non-datetime")

    def _eval_Start(self, node: dict):
        interval = self.eval(node["operand"])
        return None if interval is None else interval.effective_low()

    def _eval_End(self, node: dict):
        interval = self.eval(node["operand"])
        return None if interval is None else interval.effective_high()

    def _eval_Predecessor(self, node: dict):
        value = self.eval(node["operand"])
        return None if value is None else _step(value, -1)

    def _eval_Successor(self, node: dict):
        value = self.eval(node["operand"])
        return None if value is None else _step(value, +1)

    def _eval_DurationBetween(self, node: dict):
        low, high = self._operands(node)
        if low is None or high is None:
            return None
        precision = node.get("precision")
        low_dt, high_dt = self._to_datetime(low), self._to_datetime(high)
        if precision in ("Year", "Month"):
            months = (high_dt.year - low_dt.year) * 12 + (high_dt.month - low_dt.month)
            if (high_dt.day, high_dt.hour) < (low_dt.day, low_dt.hour):
                months -= 1
            return months // 12 if precision == "Year" else months
        delta = high_dt - low_dt
        if precision == "Day":
            return delta.days
        if precision == "Week":
            return delta.days // 7
        if precision == "Hour":
            return int(delta.total_seconds() // 3600)
        if precision == "Minute":
            return int(delta.total_seconds() // 60)
        if precision == "Second":
            return int(delta.total_seconds())
        raise UnsupportedElm(f"DurationBetween precision {precision!r}")

    def _eval_DifferenceBetween(self, node: dict):
        low, high = self._operands(node)
        if low is None or high is None:
            return None
        precision = node.get("precision")
        lk = _prec_key(self._to_datetime(low), precision)
        hk = _prec_key(self._to_datetime(high), precision)
        if precision == "Year":
            return hk[0] - lk[0]
        if precision == "Month":
            return (hk[0] - lk[0]) * 12 + (hk[1] - lk[1])
        if precision in ("Day", "Week"):
            days = (dt.date(*hk[:3]) - dt.date(*lk[:3])).days
            return days // 7 if precision == "Week" else days
        raise UnsupportedElm(f"DifferenceBetween precision {precision!r}")

    # -- arithmetic --------------------------------------------------------------------------------

    _CALENDAR_UNITS = {"year": 12, "years": 12, "a": 12, "month": 1, "months": 1, "mo": 1}
    _FIXED_UNITS = {
        "week": dt.timedelta(weeks=1), "weeks": dt.timedelta(weeks=1),
        "wk": dt.timedelta(weeks=1),
        "day": dt.timedelta(days=1), "days": dt.timedelta(days=1), "d": dt.timedelta(days=1),
        "hour": dt.timedelta(hours=1), "hours": dt.timedelta(hours=1),
        "h": dt.timedelta(hours=1),
        "minute": dt.timedelta(minutes=1), "minutes": dt.timedelta(minutes=1),
        "min": dt.timedelta(minutes=1),
        "second": dt.timedelta(seconds=1), "seconds": dt.timedelta(seconds=1),
        "s": dt.timedelta(seconds=1),
        "millisecond": _MS, "milliseconds": _MS, "ms": _MS,
    }

    def _shift_temporal(self, value, quantity: Quantity, sign: int):
        unit = quantity.unit
        if unit in self._CALENDAR_UNITS:
            months = int(quantity.value) * self._CALENDAR_UNITS[unit] * sign
            year = value.year + (value.month - 1 + months) // 12
            month = (value.month - 1 + months) % 12 + 1
            leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
            days_in = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
            return value.replace(year=year, month=month, day=min(value.day, days_in[month - 1]))
        delta = self._FIXED_UNITS.get(unit)
        if delta is None:
            raise UnsupportedElm(f"temporal arithmetic with unit {unit!r}")
        total = delta * quantity.value * sign
        if isinstance(value, dt.datetime):
            return value + total
        return value + dt.timedelta(days=int(total.total_seconds() // 86400))

    def _arith(self, node: dict, op):
        left, right = self._operands(node)
        if left is None or right is None:
            return None
        if isinstance(left, (dt.date, dt.datetime)) and isinstance(right, Quantity) \
                and node["type"] in ("Add", "Subtract"):
            return self._shift_temporal(left, right, 1 if node["type"] == "Add" else -1)
        if isinstance(left, Quantity) and isinstance(right, Quantity):
            if left.unit != right.unit:
                raise UnsupportedElm(f"arithmetic over units {left.unit!r}/{right.unit!r}")
            return Quantity(op(left.value, right.value), left.unit)
        if isinstance(left, Quantity) and isinstance(right, (int, float)):
            return Quantity(op(left.value, right), left.unit)
        if isinstance(left, (int, float)) and isinstance(right, Quantity):
            return Quantity(op(left, right.value), right.unit)
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            return op(left, right)
        raise UnsupportedElm(f"{node['type']} over {type(left).__name__}/{type(right).__name__}")

    def _eval_Add(self, node: dict):
        return self._arith(node, lambda a, b: a + b)

    def _eval_Subtract(self, node: dict):
        return self._arith(node, lambda a, b: a - b)

    def _eval_Multiply(self, node: dict):
        return self._arith(node, lambda a, b: a * b)

    def _eval_Divide(self, node: dict):
        left, right = self._operands(node)
        if left is None or right is None:
            return None
        if isinstance(left, Quantity) and isinstance(right, Quantity):
            if left.unit != right.unit:
                raise UnsupportedElm(f"Divide over units {left.unit!r}/{right.unit!r}")
            return left.value / right.value if right.value else None
        if isinstance(left, Quantity):
            return Quantity(left.value / right, left.unit) if right else None
        return left / right if right else None

    def _eval_Negate(self, node: dict):
        value = self.eval(node["operand"])
        if value is None:
            return None
        return Quantity(-value.value, value.unit) if isinstance(value, Quantity) else -value

    def _eval_Truncate(self, node: dict):
        value = self.eval(node["operand"])
        return None if value is None else int(value)

    def _eval_Round(self, node: dict):
        value = self.eval(node["operand"])
        return None if value is None else float(round(value))

    def _eval_Abs(self, node: dict):
        value = self.eval(node["operand"])
        if value is None:
            return None
        return Quantity(abs(value.value), value.unit) if isinstance(value, Quantity) else abs(value)

    def _eval_ConvertQuantity(self, node: dict):
        quantity, unit = self._operands(node)
        if quantity is None:
            return None
        if quantity.unit == unit:
            return quantity
        raise UnsupportedElm(f"quantity conversion {quantity.unit!r}->{unit!r}")

    # -- strings ---------------------------------------------------------------------------------

    def _eval_Split(self, node: dict):
        text = self.eval(node["stringToSplit"])
        if text is None:
            return None
        return text.split(self.eval(node["separator"]))

    def _eval_Concatenate(self, node: dict):
        parts = self._operands(node)
        if any(p is None for p in parts):
            return None
        return "".join(parts)

    def _eval_Combine(self, node: dict):
        items = self.eval(node["source"])
        if items is None:
            return None
        sep = self.eval(node["separator"]) if node.get("separator") else ""
        return sep.join(x for x in items if x is not None)

    def _eval_Lower(self, node: dict):
        value = self.eval(node["operand"])
        return None if value is None else value.lower()

    def _eval_Upper(self, node: dict):
        value = self.eval(node["operand"])
        return None if value is None else value.upper()
