"""Constraint solving: a stratum target -> a skeleton patient (SYNTH-PDS-ORACLE).

The solver walks the same ELM the evaluator interprets, but *backwards*: given a demanded
truth value per population define, it recursively satisfies each expression, accumulating
constraints on decision variables — temporal domains for clinically timed elements,
numeric domains for compared values (BP components), value-set members for coded
elements, status strings, presence/absence for nullable paths.

Strategies for real measure shapes (all HEURISTIC — every solved patient is re-evaluated
through the full ELM before emission, so a wrong strategy is a hard verification failure,
never wrong data — SYNTH-PDS-VERIFY):

- **Flattening**: query sources are flattened through fluent utility functions
  (e.g., verification-status checks), nested queries, unions and cross-library defines down to their
  base retrieves; accumulated where-clauses are satisfied against the created sketch.
- **Single-sketch collapse**: a value-position define (a "lowest reading on the last day" shape) is
  solved by instantiating exactly ONE qualifying resource, which collapses temporal-ordering helpers
  (first/last/earliest/latest) and temporal predicates to that resource's own slots.
- **Function inlining**: a call to any user-defined function is solved by inlining its body
  with the arguments bound, so a helper library's interval or status utilities resolve to
  slots over the sketch with no knowledge of the helper's name. FHIRHelpers conversions are
  the one library modeled natively (they are coercions).
- **Decisive-conjunct relaxation**: falsifying an AND falsifies one chooser-picked
  conjunct and leaves the rest unconstrained (they may land either way; the stratum's
  truth is already decided). The MC/DC-style "hold others true" mode can return later.
- **Correlated instantiation + cross-slot pinning** (SYNTH-PDS-CORRELATED): a ``with …
  such that`` relationship instantiates the related resource and satisfies the
  correlation; a temporal comparison whose BOTH sides are data (``performed starts
  during day of Encounter.period``) pins the two slots to one chooser-drawn instant
  inside the intersection of their domains — a strict subset of the satisfying
  assignments, which is all a generator needs. Falsifying a correlation routes through
  "no base resource" / "no related resource" / "related resource fails the such-that".
- **Extension slots**: the query a translator emits for a QI-Core/US-Core extension
  element (``Patient.sex``, ``ProcedureNotDone.recorded``) — a filter of ``.extension``
  on ``url`` returning ``value`` — denotes one decision variable, stored as an
  ``extension:<url>`` field on the owning sketch.

Route choices are drawn deterministically from ``(seed, patient, site)``
(SYNTH-PDS-DETERMINISM); contradictions raise ``Unsatisfiable`` with their sites.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from . import SynthesizerError, Unsatisfiable, UnsupportedElm, distributions
from .evaluator import FHIR_NS, Bundle, Concept, CqlInterval, Evaluator, Quantity
from .library import Code, Env, Lib
from .valuesets import Coding, ValueSetIndex

if TYPE_CHECKING:
    from .measure_config import MeasureConfig

_DAY = dt.timedelta(days=1)
#: Solved points keep clear of interval edges: precision pins (midnight, day-of) can
#: never flip a result that sits a full day inside its satisfying window.
_MARGIN = _DAY
_ESCAPE = dt.timedelta(days=730)


class _Absent:
    """Identity-checked singleton; survives the solver's backtracking deepcopy."""

    def __repr__(self) -> str:  # pragma: no cover - repr only
        return "ABSENT"

    def __copy__(self) -> "_Absent":
        return self

    def __deepcopy__(self, memo) -> "_Absent":
        return self


ABSENT = _Absent()


class _Present:
    """Identity-checked marker for "must be populated, value otherwise unconstrained"
    (an ``is not null`` demand). A later, stronger constraint (a temporal or numeric
    domain) refines it; fill materializes any leftover with a type-appropriate default."""

    def __repr__(self) -> str:  # pragma: no cover - repr only
        return "PRESENT"

    def __copy__(self) -> "_Present":
        return self

    def __deepcopy__(self, memo) -> "_Present":
        return self


PRESENT = _Present()


class _OpenEnd:
    """Slot sentinel for ``end of`` an open-ended interval (a prevalence with no
    abatement): the effective end is the type maximum by construction, so comparisons
    against it are decidable without a decision variable."""

    def __repr__(self) -> str:  # pragma: no cover - repr only
        return "OPEN-END"


OPEN_END = _OpenEnd()


@dataclass
class TemporalDomain:
    lo: dt.datetime
    hi: dt.datetime
    sites: list[str] = field(default_factory=list)

    def narrow(self, lo: dt.datetime | None, hi: dt.datetime | None, site: str) -> None:
        if lo is not None and lo > self.lo:
            self.lo = lo
        if hi is not None and hi < self.hi:
            self.hi = hi
        self.sites.append(site)
        if self.lo > self.hi:
            raise Unsatisfiable(f"temporal domain emptied at {site}", conflicts=list(self.sites))


@dataclass
class DateDomain:
    lo: dt.date
    hi: dt.date
    sites: list[str] = field(default_factory=list)

    def narrow(self, lo: dt.date | None, hi: dt.date | None, site: str) -> None:
        if lo is not None and lo > self.lo:
            self.lo = lo
        if hi is not None and hi < self.hi:
            self.hi = hi
        self.sites.append(site)
        if self.lo > self.hi:
            raise Unsatisfiable(f"birthDate domain emptied at {site}", conflicts=list(self.sites))


@dataclass
class NumericDomain:
    lo: float
    hi: float
    unit: str
    sites: list[str] = field(default_factory=list)

    def narrow(self, lo: float | None, hi: float | None, site: str) -> None:
        if lo is not None and lo > self.lo:
            self.lo = lo
        if hi is not None and hi < self.hi:
            self.hi = hi
        self.sites.append(site)
        if self.lo > self.hi:
            raise Unsatisfiable(f"numeric domain emptied at {site}", conflicts=list(self.sites))


@dataclass
class ResourceSketch:
    """A minimal resource with only determining attributes bound.

    ``key`` identifies the retrieve it satisfies: ``(resourceType, valueset-url)`` for
    code-filtered retrieves, ``(resourceType, templateId-or-None)`` otherwise. ``fields``
    hold string values, Codes, TemporalDomains, ABSENT markers; component value domains
    are keyed ``component:<code>``.

    ``serial`` is the sketch's IDENTITY across backtracking: ``_try_routes`` rolls back
    by swapping in a deepcopy of the whole state, so a sketch reference held in a solver
    scope may be a stale twin of the live object — value equality diverges the moment a
    failed attempt mutated it, and object identity never survives the copy. Resolution
    therefore always goes serial -> current state (``Solver._index_of``).
    """
    resource_type: str
    template_id: str | None
    valueset_url: str | None
    code_property: str | None
    code: Coding | None
    serial: int = -1
    fields: dict[str, object] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str | None]:
        return (self.resource_type, self.valueset_url or self.template_id)


@dataclass
class SolveState:
    patient_fields: dict[str, object] = field(default_factory=dict)
    birth_date: DateDomain | _Absent | None = None
    resources: list[ResourceSketch] = field(default_factory=list)
    forbidden: dict[tuple[str, str | None], str] = field(default_factory=dict)
    define_wants: dict[tuple[str, str], bool | None] = field(default_factory=dict)
    define_sketch: dict[tuple[str, str], int] = field(default_factory=dict)  # -> resources index
    #: Branches that must stay empty while resources of their retrieve exist: indices into
    #: ``Solver._kept_empty``, so every resource of the retrieve, placed before or after, fails a where clause.
    kept_empty: list[int] = field(default_factory=list)
    next_serial: int = 0


#: Plausible alternates for falsifying a coded-field constraint.
_ALTERNATIVES: dict[tuple[str, str], str] = {
    ("Encounter", "status"): "planned",
    ("Observation", "status"): "preliminary",
    ("Procedure", "status"): "not-done",
    ("Condition", "clinicalStatus"): "inactive",
    ("Condition", "verificationStatus"): "confirmed",
    ("Patient", "gender"): "male",
    ("Encounter", "class"): "AMB",
}

#: Which resource types actually CARRY a clinically timed element, for the slots whose
#: FHIR ownership is unambiguous. ``Coalesce(<interval of performed>,
#: <interval of authoredOn>)`` over a ServiceRequest must resolve to ``authoredOn`` — a
#: sketch accepts any field name, so without this gate the solver would happily solve
#: ``performed`` onto a request and emit an element the type does not have.
_ELEMENT_OWNERS: dict[str, frozenset[str]] = {
    "performed": frozenset({"Procedure"}),
    "authoredOn": frozenset({"ServiceRequest", "MedicationRequest", "DeviceRequest",
                             "CarePlan", "Task", "CommunicationRequest"}),
    "effective": frozenset({"Observation", "DiagnosticReport", "MedicationStatement"}),
    "onset": frozenset({"Condition", "AllergyIntolerance"}),
    "period": frozenset({"Encounter", "Coverage", "CareTeam", "EpisodeOfCare"}),
}

#: Elements fill never writes, so a read of one is always null. A condition carries no abatement (fill.py), which
#: makes its onset-to-abatement interval open-ended.
_UNFILLED: frozenset[tuple[str, str]] = frozenset({("Condition", "abatement")})

_ELM_DATETIME = "{urn:hl7-org:elm-types:r1}DateTime"


class _Chooser:
    def __init__(self, seed: int, patient_key: str):
        self._prefix = f"{seed}:{patient_key}:"

    def _digest(self, site: str) -> int:
        digest = hashlib.blake2b((self._prefix + site).encode(), digest_size=8).digest()
        return int.from_bytes(digest, "big")

    def pick(self, site: str, n: int) -> int:
        return self._digest(site) % n

    def order(self, site: str, n: int) -> list[int]:
        first = self.pick(site, n)
        return [(first + i) % n for i in range(n)]

    def unit(self, site: str) -> float:
        """A deterministic uniform draw in [0, 1) for this decision site."""
        return self._digest(site) / 2 ** 64

    def pick_weighted(self, site: str, weights: list[float]) -> int:
        """Inverse-CDF selection (SYNTH-PDS-PRIORS). Equal weights reproduce a flat pick."""
        total = sum(weights)
        if total <= 0:
            raise SynthesizerError(f"weighted pick at {site} has no positive weight")
        threshold = self.unit(site) * total
        running = 0.0
        for index, weight in enumerate(weights):
            running += weight
            if threshold < running:
                return index
        return len(weights) - 1

    def order_weighted(self, site: str, weights: list[float]) -> list[int]:
        """Weighted sampling without replacement — a route ORDER, since a likelier route
        may still turn out unsatisfiable and the next one has to be tried."""
        remaining = list(weights)
        order: list[int] = []
        for step in range(len(weights)):
            if sum(remaining) <= 0:  # only zero-weight options left; keep them reachable
                seen = set(order)
                order.extend(i for i in range(len(weights)) if i not in seen)
                break
            index = self.pick_weighted(f"{site}#{step}", remaining)
            order.append(index)
            remaining[index] = 0.0
        return order


# ---------------------------------------------------------------------------- slot refs

@dataclass(frozen=True)
class _FieldSlot:
    """A temporal/coded field on a sketch (owner None = the patient)."""
    owner_index: int | None
    prop: str


@dataclass(frozen=True)
class _ComponentSlot:
    owner_index: int
    code: str          # component discriminator code
    system: str


@dataclass(frozen=True)
class _IntervalView:
    """An interval-valued expression over a sketch: a point element viewed as an
    instant interval, a period with fill-fixed duration, or an open-ended prevalence."""
    start: _FieldSlot
    mode: str          # "point" | "period" | "open-end"


@dataclass
class _Closure:
    node: dict
    lib: Lib
    scope: dict[str, Any]


class Solver:
    """Solves one patient's constraint system for a stratum's demanded truth values."""

    def __init__(self, env: Env | dict, valuesets: ValueSetIndex, *, seed: int, patient_key: str,
                 priors: bool = False, parameters: dict[str, Any] | None = None,
                 config: MeasureConfig | None = None):
        self.env = env if isinstance(env, Env) else Env([env])
        self.valuesets = valuesets
        self.chooser = _Chooser(seed, patient_key)
        # SYNTH-PDS-PRIORS — realistic mix mode only; uniform/adversarial keeps flat
        # selection so conformance fixtures exercise every member of every domain.
        self.priors = priors
        # SYNTH-PDS-MEASURE-CONFIG — the measure's own route and member weights; realistic mode only, like priors.
        self.config = config if priors else None
        # SYNTH-PDS-SKIPPED — routes abandoned because the solver cannot invert them,
        # on a solve that SUCCEEDED by another route. Silence here is the failure mode
        # (`CORE-NO-SILENT-MISLOWER`): the cohort looks complete while a whole exclusion
        # path is dark, and downstream cannot tell that from "the logic says empty".
        self.skipped: list[str] = []
        #: The branches ``SolveState.kept_empty`` indexes. Append-only and outside the state, so a rollback's
        #: deepcopy never copies the branches' libraries.
        self._kept_empty: list[tuple[Solver._Branch, str]] = []
        self._const = Evaluator(self.env, valuesets, Bundle(), parameters)
        self._lib = self.env.lib(self.env.root)
        period = self._const.eval_in(self.env.root, {"type": "ParameterRef",
                                                     "name": "Measurement Period"})
        if not isinstance(period, CqlInterval) or not isinstance(period.effective_low(), dt.datetime):
            raise UnsupportedElm("Measurement Period is not a DateTime interval")
        self.period_lo: dt.datetime = period.effective_low()
        self.period_hi: dt.datetime = period.effective_high()
        self.clamp_lo = self.period_lo - _ESCAPE
        self.clamp_hi = self.period_hi + _ESCAPE
        self.state = SolveState()

    # -- entry point -------------------------------------------------------------------

    def solve(self, wants: dict[str, bool | None]) -> SolveState:
        root = self.env.lib(self.env.root)
        for name in wants:
            self._target_define(root, name, wants[name], f"stratum:{name}")
        return self.state

    # -- define targeting ----------------------------------------------------------------

    def _target_define(self, lib: Lib, name: str, want: bool | None, site: str) -> None:
        key = (lib.name, name)
        recorded = self.state.define_wants
        if key in recorded:
            if recorded[key] is not want:
                raise Unsatisfiable(
                    f"define {lib.name}.{name!r} demanded both {recorded[key]} and {want}",
                    conflicts=[site, f"define:{lib.name}.{name}"])
            return
        recorded[key] = want
        define = lib.defines.get(name)
        if define is None:
            raise SynthesizerError(f"define {name!r} not in library {lib.name}")
        saved = self._lib
        self._lib = lib
        try:
            self._satisfy(define["expression"], want, {})
        finally:
            self._lib = saved

    # -- helpers -----------------------------------------------------------------------------

    def _const_eval(self, node: dict):
        return self._const.eval_in(self._lib.name, node)

    def _index_of(self, sketch: ResourceSketch) -> int:
        """The sketch's index in the LIVE state, resolved by serial — the reference in
        hand may be a stale pre-rollback twin (see ResourceSketch.serial)."""
        for index, live in enumerate(self.state.resources):
            if live.serial == sketch.serial:
                return index
        raise SynthesizerError(
            f"sketch serial {sketch.serial} ({sketch.resource_type}) not in the live state")

    def _in_lib(self, lib: Lib):
        solver = self

        class _Ctx:
            def __enter__(self):
                self.saved = solver._lib
                solver._lib = lib

            def __exit__(self, *exc):
                solver._lib = self.saved

        return _Ctx()

    def _try_routes(self, site: str, options: list, attempt,
                    weights: list[float] | None = None) -> None:
        """Try options in chooser-rotated order with state rollback.

        A route that hits an UNSUPPORTED construct is a failed route, not a dead
        stratum — another route may be solvable (e.g. the non-correlated branch of a
        union). Only when every route fails unsupported does the error stay loud.

        Such a skip is RECORDED on ``self.skipped`` (SYNTH-PDS-SKIPPED) even when a
        sibling rescues the solve: an exclusion path the solver cannot invert is a
        permanently dark branch of the measure, and a cohort that never says so reads as
        complete. Unsatisfiable routes are NOT recorded — those are ordinary
        backtracking, not a gap in the solver.

        ``weights`` (SYNTH-PDS-PRIORS) reorders the routes when they are element-value
        assignments with a known real-world mix. Every route remains reachable — a prior
        changes which satisfying patient gets built, never which strata are satisfiable.
        """
        failures: list[str] = []
        unsupported: list[str] = []
        any_unsat = False
        order = (self.chooser.order_weighted(site, weights) if weights
                 else self.chooser.order(site, len(options)))
        for index in order:
            saved = copy.deepcopy(self.state)
            try:
                attempt(options[index])
                self.skipped.extend(unsupported)
                return
            except Unsatisfiable as e:
                any_unsat = True
                self.state = saved
                failures.append(str(e))
            except UnsupportedElm as e:
                self.state = saved
                failures.append(f"unsupported: {e}")
                unsupported.append(str(e))
        if any_unsat:
            raise Unsatisfiable(f"no route satisfies {site}", conflicts=failures)
        # Sorted, so the message names the same gap the same way whatever order the
        # chooser tried the routes in — otherwise one gap reports as several
        # (SYNTH-PDS-SKIPPED aggregates these by text).
        raise UnsupportedElm(f"no supported route at {site}: " + "; ".join(sorted(failures)))

    # -- SYNTH-PDS-PRIORS: what element does a route option decide? -----------------------
    #
    # Weighting a route requires knowing what it would assign BEFORE attempting it, and
    # `_slot()` is not safe to call speculatively — through `_single_slot` it can
    # instantiate sketches. So the peek walks only the shapes that resolve to an already
    # bound alias or the patient, and answers None for everything else. None means
    # uniform, which is the correct answer for a logic route (which conjunct falsifies an
    # AND, which exclusion a patient takes) — those are measure-shaped and out of scope.

    _PEEK_WRAPPERS = frozenset({"As", "ToConcept", "ToString", "ToDateTime", "ToDate",
                                "ToQuantity", "ToDecimal", "ToInteger"})

    def _peek_element(self, node: dict, scope: dict) -> tuple[str, str] | None:
        """``(resourceType, element)`` for a plain property access, else None."""
        if not isinstance(node, dict):
            return None
        kind = node.get("type")
        if kind in self._PEEK_WRAPPERS:
            return self._peek_element(node["operand"], scope)
        if kind == "FunctionRef":
            alias = node.get("libraryName")
            target = self._lib.includes.get(alias, alias) if alias else None
            operands = node.get("operand") or []
            if target == "FHIRHelpers" and len(operands) == 1:
                return self._peek_element(operands[0], scope)
            return None
        if kind != "Property":
            return None
        path = node["path"].split(".")[0]
        if node.get("scope"):
            owner = scope.get(node["scope"])
            return (owner.resource_type, path) if isinstance(owner, ResourceSketch) else None
        source = node.get("source")
        if isinstance(source, dict) and source.get("type") == "Property" \
                and node["path"] == "value":
            return self._peek_element(source, scope)
        if isinstance(source, dict) and source.get("type") == "ExpressionRef" \
                and source.get("name") == "Patient" and not source.get("libraryName"):
            return ("Patient", path)
        return None

    def _element_route_weights(self, options: list[dict], scope: dict) -> list[float] | None:
        """Weights for a disjunction whose arms each pin one element to one code."""
        if not self.priors:
            return None
        target: tuple[str, str] | None = None
        values: list[str] = []
        for option in options:
            if not isinstance(option, dict) or option.get("type") not in ("Equal", "Equivalent"):
                return None
            element = self._peek_element(option["operand"][0], scope)
            if element is None or (target is not None and element != target):
                return None
            target = element
            try:
                expected = self._const_eval(option["operand"][1])
            except (UnsupportedElm, SynthesizerError, Unsatisfiable):
                return None
            if isinstance(expected, Concept):
                expected = expected.codes[0] if expected.codes else None
            code = expected.code if isinstance(expected, Code) else expected
            if not isinstance(code, str):
                return None
            values.append(code)
        return distributions.element_weights(target[0], target[1], values) if target else None

    def _presence_weights(self, antecedent: dict, scope: dict) -> list[float] | None:
        """Weights for ``X is not null implies P`` — an element PRESENCE decision, so the
        element's own absence rate decides it. Ordered (antecedent-false, consequent-true)
        to match the route list."""
        if not self.priors:
            return None
        node = antecedent
        if isinstance(node, dict) and node.get("type") == "Not":
            node = node["operand"]
        if not isinstance(node, dict) or node.get("type") != "IsNull":
            return None
        element = self._peek_element(node["operand"], scope)
        if element is None:
            return None
        prior = distributions.ELEMENT_PRIORS.get(element)
        if prior is None:
            return None
        absent = prior.get(distributions.ABSENT_VALUE, 0.0)
        present = sum(w for key, w in prior.items() if key != distributions.ABSENT_VALUE)
        return [absent, present] if absent > 0 and present > 0 else None

    @staticmethod
    def _conjuncts(node: dict, op: str) -> list[dict]:
        parts: list[dict] = []
        for operand in node["operand"]:
            if operand.get("type") == op:
                parts.extend(Solver._conjuncts(operand, op))
            else:
                parts.append(operand)
        return parts

    # -- recursive satisfaction ------------------------------------------------------------

    def _satisfy(self, node: dict, want: bool | None, scope: dict[str, Any]) -> None:
        kind = node.get("type")
        handler = getattr(self, f"_satisfy_{kind}", None)
        if handler is None:
            raise UnsupportedElm(
                f"cannot solve for ELM node type {kind!r} (library {self._lib.name}, "
                f"locator {node.get('locator')})")
        handler(node, want, scope)

    def _satisfy_Literal(self, node: dict, want, scope) -> None:
        value = self._const_eval(node)
        if value is not want:
            raise Unsatisfiable(f"literal {value!r} cannot be {want!r}")

    def _satisfy_And(self, node: dict, want, scope) -> None:
        parts = self._conjuncts(node, "And")
        site = f"and@{self._lib.name}:{node.get('locator')}"
        if want is True:
            for part in parts:
                self._satisfy(part, True, scope)
            return

        # False: one decisive conjunct false, the rest unconstrained (the completed
        # bundle's ground truth is exact regardless — SYNTH-PDS-VERIFY).
        # Null: one conjunct null; the rest NOT false, so no false wins the AND (CQL spec,
        # Logical Operators, "And": null and null is null). True is tried first; null is the
        # route when the rest read the same missing element (`age >= 30 and age <= 70`).
        def attempt(chosen: dict) -> None:
            self._satisfy(chosen, want, scope)
            if want is None:
                for index, part in enumerate(parts):
                    if part is not chosen:
                        self._try_routes(f"{site}:not-false#{index}", [True, None],
                                         lambda w, p=part: self._satisfy(p, w, scope), [1.0, 0.0])

        self._try_routes(site, parts, attempt)

    def _satisfy_Or(self, node: dict, want, scope) -> None:
        parts = self._conjuncts(node, "Or")
        site = f"or@{self._lib.name}:{node.get('locator')}"
        if want is False:
            for part in parts:
                self._satisfy(part, False, scope)
            return

        # Null: one disjunct null; the rest NOT true (CQL spec, Logical Operators, "Or"). False is
        # tried first, null when the rest read the same missing element.
        def attempt(chosen: dict) -> None:
            self._satisfy(chosen, want, scope)
            if want is None:
                for index, part in enumerate(parts):
                    if part is not chosen:
                        self._try_routes(f"{site}:not-true#{index}", [False, None],
                                         lambda w, p=part: self._satisfy(p, w, scope), [1.0, 0.0])

        # Only the want-True arms are alternative VALUES for one element; want-None picks
        # which arm goes null, a different decision the element mix says nothing about.
        weights = None
        if want is True:
            weights = (self.config.route_weights(self._lib.name, node.get("locator")) if self.config else None) \
                or self._element_route_weights(parts, scope)
        self._try_routes(site, parts, attempt, weights)

    def _satisfy_Not(self, node: dict, want, scope) -> None:
        self._satisfy(node["operand"], None if want is None else (not want), scope)

    def _satisfy_Implies(self, node: dict, want, scope) -> None:
        left, right = node["operand"]
        site = f"implies@{self._lib.name}:{node.get('locator')}"
        if want is True:
            def attempt(route) -> None:
                if route == "antecedent-false":
                    self._satisfy(left, False, scope)
                else:
                    self._satisfy(right, True, scope)
            self._try_routes(site, ["antecedent-false", "consequent-true"], attempt,
                             self._presence_weights(left, scope))
            return
        if want is False:
            self._satisfy(left, True, scope)
            self._satisfy(right, False, scope)
            return
        raise Unsatisfiable(f"implies-null not solvable at {site}")

    def _satisfy_IsNull(self, node: dict, want, scope) -> None:
        site = f"is-null@{self._lib.name}:{node.get('locator')}"
        try:
            slot = self._slot(node["operand"], scope)
        except UnsupportedElm:
            slot = None
        if slot is not None and not isinstance(slot, (_IntervalView, _ComponentSlot)) \
                and getattr(slot, "prop", None):
            if want is True:
                self._assign(slot, ABSENT, site)
                return
            fields, prop, _ = self._fields_of(slot)
            if fields.get(prop) is ABSENT:
                raise Unsatisfiable(f"{prop} demanded both absent and present",
                                    conflicts=[site])
            if fields.get(prop) is None and getattr(slot, "owner_index", None) is not None:
                # `X is not null` with nothing else constraining X: presence itself is
                # the demand; fill materializes a type-appropriate value (PRESENT).
                fields[prop] = PRESENT
            return
        # Not a simple field: the operand is a resource-valued chain. Null-ness then
        # means "no qualifying resource" — forbid the branches (or instantiate for
        # not-null), which the flattener knows how to reach.
        branches = self._flatten(node["operand"], scope)
        if want is True:
            for branch in branches:
                self._forbid(branch, site)
            return
        def attempt(branch) -> None:
            self._instantiate(branch, site)
        self._try_routes(site, branches, attempt)

    def _satisfy_ExpressionRef(self, node: dict, want, scope) -> None:
        lib = self.env.resolve(self._lib, node.get("libraryName"))
        self._target_define(lib, node["name"], want, f"ref:{lib.name}.{node['name']}")

    def _satisfy_FunctionRef(self, node: dict, want, scope) -> None:
        alias = node.get("libraryName")
        if alias and self._lib.includes.get(alias, alias) == "FHIRHelpers":
            raise UnsupportedElm(f"cannot solve boolean FHIRHelpers.{node['name']}")
        lib = self.env.resolve(self._lib, alias)
        fn = self._resolve_function(lib, node["name"], len(node.get("operand", [])))
        bindings = {op["name"]: _Closure(arg, self._lib, dict(scope))
                    for op, arg in zip(fn["operand"], node["operand"])}
        with self._in_lib(lib):
            self._satisfy(fn["expression"], want, bindings)

    def _resolve_function(self, lib: Lib, name: str, arity: int) -> dict:
        overloads = [f for f in lib.functions.get(name, [])
                     if len(f.get("operand", [])) == arity]
        if len(overloads) != 1:
            raise UnsupportedElm(
                f"function {lib.name}.{name}/{arity}: {len(overloads)} candidates")
        return overloads[0]

    def _satisfy_OperandRef(self, node: dict, want, scope) -> None:
        closure = scope.get(node["name"])
        if not isinstance(closure, _Closure):
            raise UnsupportedElm(f"operand {node['name']!r} is not an expression binding")
        with self._in_lib(closure.lib):
            self._satisfy(closure.node, want, closure.scope)

    # -- exists / retrieves ---------------------------------------------------------------------

    def _satisfy_Exists(self, node: dict, want, scope) -> None:
        self._satisfy_populated(node, node["operand"], want, scope, "exists")

    def _satisfy_Query(self, node: dict, want, scope) -> None:
        """A query in TRUTH position — a list-valued define targeted directly rather than
        through ``exists`` (``define "CompoundProcedureList": ([Procedure: X]
        union [Procedure: Y]).performanceStatusCheck() P where …``).

        The decision is identical to ``exists``: populate the list or keep it empty. The
        flattener already reduces unions and fluent source functions to base retrieves, so
        this shares one implementation rather than restating it (`PROC-DRY`).
        """
        self._satisfy_populated(node, node, want, scope, "query")

    def _satisfy_populated(self, node: dict, source: dict, want, scope, kind: str) -> None:
        """Drive a list-valued expression to non-empty (``want`` True) or empty (False)."""
        if want is None:
            raise Unsatisfiable(f"{kind} never evaluates to null",
                                conflicts=[f"{kind}@{node.get('locator')}"])
        site = f"{kind}@{self._lib.name}:{node.get('locator')}"
        if self._is_component_query(source):
            self._populate_component(self._query_slot(source, scope), want, site)
            return
        element = self._property_list_query(source, scope)
        if element is not None:
            self._populate_property_list(element, source, want, site)
            return
        branches = self._flatten(source, scope)
        if want is True:
            def attempt(branch) -> None:
                self._instantiate(branch, site)
            self._try_routes(site, branches, attempt)
            return
        # want False: no branch may produce a member; a correlated branch has escape
        # routes that keep its (possibly shared) base resource alive.
        for branch in branches:
            if branch.with_rels:
                self._uncorrelate(branch, site)
            else:
                self._keep_empty(branch, site)

    def _keep_empty(self, branch: "Solver._Branch", site: str) -> None:
        """Drive a branch to empty. While no resource of its retrieve exists, the retrieve is forbidden.

        Once one does, it is a resource another define needs (``[Encounter] Discharge where …`` while the
        patient's qualifying visit is an Encounter): that resource, and any of the retrieve placed later
        (``_instantiate``), is made to fail one of the branch's where clauses instead.
        """
        existing = self._matching_sketches(branch)
        if not existing:
            self._forbid(branch, site)
            return
        if not branch.wheres:
            raise Unsatisfiable(f"an unfiltered retrieve must stay empty at {site}", conflicts=[site])
        self._kept_empty.append((branch, site))
        self.state.kept_empty.append(len(self._kept_empty) - 1)
        for sketch in existing:
            self._miss(branch, sketch, site)

    def _miss(self, branch: "Solver._Branch", sketch: ResourceSketch, site: str) -> None:
        """Keep one resource out of a branch: one of the branch's where clauses fails for it (a where keeps only a
        true result, so false is enough)."""
        def attempt(where) -> None:
            w_alias, w_node, w_lib, w_scope = where
            with self._in_lib(w_lib):
                self._satisfy(w_node, False, {**w_scope, w_alias: sketch})

        self._try_routes(f"miss@{site}:{sketch.serial}", branch.wheres, attempt)

    @staticmethod
    def _is_component_query(source: dict) -> bool:
        """A query over a resource's component list (``Reading.component Systolic where …``)."""
        sources = source.get("source", []) if source.get("type") == "Query" else []
        if len(sources) != 1:
            return False
        src = sources[0]["expression"]
        return src.get("type") == "Property" and src["path"].split(".")[0] == "component"

    def _populate_component(self, slot: "_ComponentSlot", want: bool, site: str) -> None:
        """``exists`` over a component query: the component with the where clause's code is present (a later
        comparison of its value refines that into a numeric domain), or absent."""
        fields = self.state.resources[slot.owner_index].fields
        key = f"component:{slot.code}"
        current = fields.get(key)
        if want is True:
            if current is ABSENT:
                raise Unsatisfiable(f"component {slot.code} demanded both absent and present", conflicts=[site])
            if current is None:
                fields[key] = PRESENT
                fields.setdefault(f"component-system:{slot.code}", slot.system)
            return
        if current is not None and current is not ABSENT:
            raise Unsatisfiable(f"component {slot.code} demanded both present and absent", conflicts=[site])
        fields[key] = ABSENT

    def _property_list_query(self, source: dict, scope: dict) -> "_FieldSlot | None":
        """The field slot a query over a LIST-VALUED ELEMENT of a bound resource denotes
        (``MedicationsNotDocumented.reasonCode reasonItem where reasonItem ~ X``), or
        None when the source is not that shape. Extension and component sub-queries have
        their own machinery and are excluded here."""
        if source.get("type") != "Query":
            return None
        # Unwrap to the Property the query ranges over: the translator often nests a
        # per-element coercion query (`reasonCode $this return ToConcept($this)`) under
        # the authored alias.
        src = source
        while isinstance(src, dict):
            kind = src.get("type")
            if kind in ("As", "ToList"):
                src = src["operand"]
                continue
            if kind == "Query":
                sources = src.get("source", [])
                if len(sources) != 1:
                    return None
                src = sources[0]["expression"]
                continue
            break
        if not isinstance(src, dict) or src.get("type") != "Property" \
                or not src.get("scope"):
            return None
        path = src["path"].split(".")[0]
        if path in ("extension", "component"):
            return None
        owner = scope.get(src["scope"])
        if not isinstance(owner, ResourceSketch):
            return None
        return _FieldSlot(self._index_of(owner), path)

    def _populate_property_list(self, slot: "_FieldSlot", source: dict, want: bool,
                                site: str) -> None:
        """Satisfy ``exists`` over a coded list element: pick the demanded code from the
        query's own where clause (a CodeRef comparison or value-set membership) and
        place it in the list, or demand the element absent."""
        if want is False:
            self._assign(slot, ABSENT, site)
            return
        where = source.get("where")
        code = self._demanded_code(where, site) if where is not None else None
        if code is None:
            raise UnsupportedElm(
                f"cannot determine the demanded member of {slot.prop!r} at {site}")
        fields, prop, _ = self._fields_of(slot)
        current = fields.get(prop)
        if current is ABSENT:
            raise Unsatisfiable(f"{prop} demanded both absent and populated",
                                conflicts=[site])
        if current is None:
            fields[prop] = [code]
        elif isinstance(current, list):
            if code not in current:
                fields[prop] = [*current, code]
        else:
            raise Unsatisfiable(f"{prop} holds {current!r}, not a coded list",
                                conflicts=[site])

    def _demanded_code(self, where: dict, site: str) -> Code | None:
        """The Code a list-element where clause demands: the first CodeRef comparison
        constant, else a chooser-picked member of the first referenced value set."""
        def find(node, kind):
            if isinstance(node, dict):
                if node.get("type") == kind:
                    return node
                for value in node.values():
                    hit = find(value, kind)
                    if hit is not None:
                        return hit
            elif isinstance(node, list):
                for item in node:
                    hit = find(item, kind)
                    if hit is not None:
                        return hit
            return None

        ref = find(where, "CodeRef")
        if ref is not None:
            code = self._const_eval(ref)
            return code if isinstance(code, Code) else None
        vs = find(where, "ValueSetRef")
        if vs is not None:
            vs_lib = self.env.resolve(self._lib, vs.get("libraryName"))
            url = vs_lib.valuesets.get(vs["name"])
            if url is not None:
                member = self._pick_member(url, site)
                return Code(system=member.system, code=member.code, display=member.display)
        return None

    def _pick_member(self, url: str, site: str) -> Coding:
        """A value-set member for a solved coded element — priors-weighted in realistic
        mode (SYNTH-PDS-PRIORS), flat otherwise, exactly like retrieve instantiation."""
        members = self.valuesets.members(url)
        if not members:
            raise SynthesizerError(f"value set {url} has no expansion members")
        if self.priors:
            draw = self.chooser.unit("home-system")
            return members[self.chooser.pick_weighted(
                site, self._member_weights(url, members, draw))]
        return members[self.chooser.pick(site, len(members))]

    def _member_weights(self, url: str, members: list[Coding], draw: float) -> list[float]:
        """The FHIR-level member prior, times the measure's own member weights where it states them."""
        weights = distributions.member_weights(members, draw)
        multipliers = self.config.member_multipliers(url) if self.config else None
        if not multipliers:
            return weights
        return [w * multipliers.get((m.system, m.code), 1.0) for w, m in zip(weights, members)]

    @dataclass
    class _Branch:
        retrieve: dict
        lib: Lib
        wheres: list[tuple[str, dict, Lib, dict]]     # (alias, where-node, lib, scope)
        without: list[tuple[dict, Lib, dict]]         # related sources to keep empty
        define_key: tuple[str, str] | None = None     # cache key when via ExpressionRef
        #: With relationships to correlate: (rel-node, outer alias, lib, scope)
        #: (SYNTH-PDS-CORRELATED).
        with_rels: list[tuple[dict, str, Lib, dict]] = field(default_factory=list)

    def _flatten(self, node: dict, scope: dict, alias: str | None = None,
                 wheres: list | None = None, define_key=None) -> list["Solver._Branch"]:
        """Flatten a list-valued expression to its base retrieve branches."""
        wheres = list(wheres or [])
        kind = node.get("type")
        if kind == "Retrieve":
            return [self._Branch(retrieve=node, lib=self._lib, wheres=wheres,
                                 without=[], define_key=define_key, with_rels=[])]
        if kind == "Union":
            out = []
            for op in node["operand"]:
                out.extend(self._flatten(op, scope, alias, wheres, define_key))
            return out
        if kind == "Query":
            sources = node.get("source", [])
            if len(sources) != 1:
                raise UnsupportedElm("multi-source query")
            q_alias = sources[0]["alias"]
            q_wheres = list(wheres)
            if node.get("let"):
                raise UnsupportedElm("query let in solve position")
            if node.get("where") is not None:
                q_wheres.append((q_alias, node["where"], self._lib, dict(scope)))
            branches = self._flatten(sources[0]["expression"], scope, q_alias,
                                     q_wheres, define_key)
            for rel in node.get("relationship") or []:
                for branch in branches:
                    if rel.get("type") == "Without":
                        branch.without.append((rel["expression"], self._lib, dict(scope)))
                    elif rel.get("type") == "With":
                        branch.with_rels.append((rel, q_alias, self._lib, dict(scope)))
                    else:
                        raise UnsupportedElm(f"relationship {rel.get('type')}")
            return branches
        if kind == "ExpressionRef":
            lib = self.env.resolve(self._lib, node.get("libraryName"))
            define = lib.defines.get(node["name"])
            if define is None:
                raise SynthesizerError(f"define {node['name']!r} not in {lib.name}")
            with self._in_lib(lib):
                inner = self._flatten(define["expression"], {}, None, None,
                                      define_key=(lib.name, node["name"]))
            for branch in inner:
                branch.wheres.extend(wheres)
            return inner
        if kind == "FunctionRef":
            alias_name = node.get("libraryName")
            if alias_name and self._lib.includes.get(alias_name, alias_name) == "FHIRHelpers":
                raise UnsupportedElm(f"FHIRHelpers.{node['name']} in source position")
            lib = self.env.resolve(self._lib, alias_name)
            fn = self._resolve_function(lib, node["name"], len(node.get("operand", [])))
            body = fn["expression"]
            if body.get("type") != "Query":
                raise UnsupportedElm(
                    f"source function {lib.name}.{node['name']} is not a query")
            sources = body.get("source", [])
            if len(sources) != 1 or sources[0]["expression"].get("type") != "OperandRef":
                raise UnsupportedElm(
                    f"source function {lib.name}.{node['name']} does not filter its operand")
            fn_alias = sources[0]["alias"]
            fn_wheres = list(wheres)
            if body.get("where") is not None:
                fn_wheres.append((fn_alias, body["where"], lib, {}))
            if body.get("return") is not None or body.get("relationship"):
                raise UnsupportedElm(
                    f"source function {lib.name}.{node['name']} has return/relationship")
            return self._flatten(node["operand"][0], scope, alias, fn_wheres, define_key)
        if kind == "As":
            return self._flatten(node["operand"], scope, alias, wheres, define_key)
        if kind in ("First", "Last"):
            # Single-sketch collapse: the one instantiated element IS the first/last.
            return self._flatten(node["source"], scope, alias, wheres, define_key)
        if kind == "SingletonFrom":
            return self._flatten(node["operand"], scope, alias, wheres, define_key)
        if kind == "ToList":
            return self._flatten(node["operand"], scope, alias, wheres, define_key)
        raise UnsupportedElm(
            f"cannot flatten {kind!r} to retrieves (library {self._lib.name}, "
            f"locator {node.get('locator')})")

    def _retrieve_parts(self, retrieve: dict, lib: Lib):
        """(resource_type, ident, code_property, templateId, direct_codings).

        ``ident`` keys the retrieve for forbid/instantiate purposes: a value-set URL, a
        ``code:<system>|<code>[,...]`` key for direct-reference-code filters, or None."""
        data_type = retrieve["dataType"]
        if not data_type.startswith(FHIR_NS):
            raise UnsupportedElm(f"Retrieve of non-FHIR type {data_type}")
        resource_type = data_type.removeprefix(FHIR_NS)
        codes = retrieve.get("codes")
        if codes is None:
            return resource_type, None, retrieve.get("codeProperty"), \
                retrieve.get("templateId"), []
        if codes.get("type") == "ValueSetRef":
            vs_lib = self.env.resolve(lib, codes.get("libraryName"))
            url = vs_lib.valuesets.get(codes["name"])
            if url is None:
                raise SynthesizerError(f"value set {codes['name']!r} not in {vs_lib.name}")
            return resource_type, url, retrieve.get("codeProperty"), \
                retrieve.get("templateId"), []
        direct = self._const.eval_in(lib.name, codes)
        codings = [Coding(system=c.system, code=c.code, display=c.display)
                   for c in (direct if isinstance(direct, list) else [direct])
                   if isinstance(c, Code)]
        if not codings:
            raise UnsupportedElm(f"retrieve codes filter {codes.get('type')}")
        ident = "code:" + ",".join(sorted(f"{c.system}|{c.code}" for c in codings))
        return resource_type, ident, retrieve.get("codeProperty"), \
            retrieve.get("templateId"), codings

    def _instantiate(self, branch: "Solver._Branch", site: str, *,
                     correlate: bool = True) -> ResourceSketch:
        if branch.define_key and branch.define_key in self.state.define_sketch:
            sketch = self.state.resources[self.state.define_sketch[branch.define_key]]
            if correlate and branch.with_rels:
                self._correlate(branch, sketch, site)
            return sketch
        resource_type, ident, code_property, template, direct = self._retrieve_parts(
            branch.retrieve, branch.lib)
        key = (resource_type, ident or template)
        # A forbidden (type, templateId) blocks every sketch that would EMIT that
        # profile, not only the retrieve that recorded it — a code-filtered retrieve of
        # the same profile produces a resource the profile-level forbid still matches.
        if key in self.state.forbidden or (resource_type, None) in self.state.forbidden \
                or (template and (resource_type, template) in self.state.forbidden):
            raise Unsatisfiable(f"{key} demanded present and absent",
                                conflicts=[site, self.state.forbidden.get(key, "")])
        coding = None
        members = direct
        if ident is not None and not direct:
            members = self.valuesets.members(ident)
        if members:
            # Value sets overlap (a referral code can belong to both a "Follow Up" and
            # a "Referrals Where…" set), so a member must clear every retrieve this
            # solve promised would stay empty — key equality alone is not enough.
            members = [m for m in members
                       if not self._code_forbidden(resource_type, m)]
            if not members:
                raise Unsatisfiable(
                    f"every member of {ident} is claimed by a forbidden retrieve",
                    conflicts=[site])
        if members:
            code_site = f"code:{site}:{len(self.state.resources)}"
            if self.priors:
                # SYNTH-PDS-PRIORS — the member is arbitrary as far as the logic is
                # concerned, so let the patient's source system decide it: one home
                # coding system per patient (NOT per site, or the clustering averages
                # back out), Zipf within each system.
                draw = self.chooser.unit("home-system")
                coding = members[self.chooser.pick_weighted(
                    code_site, self._member_weights(ident, members, draw))]
            else:
                coding = members[self.chooser.pick(code_site, len(members))]
        sketch = ResourceSketch(resource_type=resource_type, template_id=template,
                                valueset_url=ident, code_property=code_property, code=coding,
                                serial=self.state.next_serial)
        self.state.next_serial += 1
        self.state.resources.append(sketch)
        index = len(self.state.resources) - 1
        if branch.define_key:
            self.state.define_sketch[branch.define_key] = index
        for w_alias, w_node, w_lib, w_scope in branch.wheres:
            with self._in_lib(w_lib):
                self._satisfy(w_node, True, {**w_scope, w_alias: sketch})
        for kept in list(self.state.kept_empty):
            kept_branch, kept_site = self._kept_empty[kept]
            if self._retrieves(kept_branch, sketch):
                self._miss(kept_branch, sketch, kept_site)
        for rel_node, rel_lib, rel_scope in branch.without:
            with self._in_lib(rel_lib):
                for rel_branch in self._flatten(rel_node, rel_scope):
                    self._forbid(rel_branch, f"{site}:without")
        if correlate and branch.with_rels:
            self._correlate(branch, sketch, site)
        return sketch

    def _correlate(self, branch: "Solver._Branch", sketch: ResourceSketch,
                   site: str) -> None:
        """Satisfy each With relationship for an instantiated base resource
        (SYNTH-PDS-CORRELATED): instantiate one related resource per relationship and
        drive the such-that true with both aliases in scope."""
        for rel_node, outer_alias, rel_lib, rel_scope in branch.with_rels:
            rel_alias = rel_node["alias"]
            rel_site = f"with@{rel_lib.name}:{rel_node.get('locator')}"
            with self._in_lib(rel_lib):
                related_branches = self._flatten(rel_node["expression"], rel_scope)

            def attempt(related_branch) -> None:
                related = self._instantiate(related_branch, rel_site)
                with self._in_lib(rel_lib):
                    self._satisfy(rel_node["suchThat"], True,
                                  {**rel_scope, outer_alias: sketch, rel_alias: related})

            self._try_routes(rel_site, related_branches, attempt)

    def _uncorrelate(self, branch: "Solver._Branch", site: str) -> None:
        """Drive a With-related query branch to empty WITHOUT forbidding its base
        retrieve: either no related resource can exist, or one exists and fails the
        such-that (the base resource — typically shared with the initial population via
        its define — stays untouched)."""
        def forbid_related(_) -> None:
            for rel_node, _outer, rel_lib, rel_scope in branch.with_rels:
                with self._in_lib(rel_lib):
                    for related_branch in self._flatten(rel_node["expression"], rel_scope):
                        self._forbid(related_branch, f"{site}:with-empty")

        def falsify_such_that(_) -> None:
            base = self._instantiate(branch, site, correlate=False)
            for rel_node, outer_alias, rel_lib, rel_scope in branch.with_rels:
                rel_alias = rel_node["alias"]
                rel_site = f"with-miss@{rel_lib.name}:{rel_node.get('locator')}"
                with self._in_lib(rel_lib):
                    related_branches = self._flatten(rel_node["expression"], rel_scope)

                def miss(related: ResourceSketch) -> None:
                    with self._in_lib(rel_lib):
                        self._satisfy(rel_node["suchThat"], False,
                                      {**rel_scope, outer_alias: base, rel_alias: related})

                def attempt(related_branch) -> None:
                    miss(self._instantiate(related_branch, rel_site, correlate=False))

                self._try_routes(rel_site, related_branches, attempt)
                # A resource ANOTHER define already placed can also match the related
                # retrieve and rescue the correlation — the such-that must fail for
                # every candidate, not just the one built here. Over-falsifying a
                # candidate the branch's own where would drop anyway only tightens; if
                # it cannot be falsified, this route is unsatisfiable and a sibling
                # (forbid-base) takes over.
                fresh = {self._index_of(base)}
                for related_branch in related_branches:
                    for sketch in self._matching_sketches(related_branch):
                        index = self._index_of(sketch)
                        if index in fresh:
                            continue
                        fresh.add(index)
                        miss(sketch)

        def forbid_base(_) -> None:
            self._forbid(branch, site)

        self._try_routes(f"unmatch@{site}", [forbid_base, forbid_related,
                                             falsify_such_that],
                         lambda route: route(None))

    def _matching_sketches(self, branch: "Solver._Branch") -> list[ResourceSketch]:
        """Existing sketches a branch's base retrieve would return."""
        return [sketch for sketch in self.state.resources if self._retrieves(branch, sketch)]

    def _retrieves(self, branch: "Solver._Branch", sketch: ResourceSketch) -> bool:
        """Whether a branch's base retrieve would return the sketch."""
        resource_type, ident, _, template, direct = self._retrieve_parts(
            branch.retrieve, branch.lib)
        if sketch.resource_type != resource_type:
            return False
        if ident is None:
            return template is None or template == sketch.template_id
        return sketch.key == (resource_type, ident) or self._sketch_code_matches(sketch, ident, direct)

    def _code_forbidden(self, resource_type: str, coding: Coding) -> bool:
        """Whether a candidate coding falls inside any forbidden retrieve's filter for
        this resource type (the membership mirror of ``_sketch_code_matches``)."""
        for (forbidden_type, ident) in self.state.forbidden:
            if forbidden_type != resource_type or ident is None:
                continue
            if ident.startswith("code:"):
                pairs = {tuple(pair.split("|", 1))
                         for pair in ident.removeprefix("code:").split(",")}
                if (coding.system, coding.code) in pairs:
                    return True
            elif self.valuesets.contains(ident, coding.system, coding.code):
                return True
        return False

    def _forbid(self, branch: "Solver._Branch", site: str) -> None:
        resource_type, ident, _, template, direct = self._retrieve_parts(
            branch.retrieve, branch.lib)
        key = (resource_type, ident or template)
        for sketch in self.state.resources:
            if sketch.resource_type != resource_type:
                continue
            if key[1] is None or sketch.key == key \
                    or (ident is None and template == sketch.template_id) \
                    or self._sketch_code_matches(sketch, ident, direct):
                raise Unsatisfiable(f"{key} already instantiated", conflicts=[site])
        self.state.forbidden[key] = site

    def _sketch_code_matches(self, sketch: ResourceSketch, ident: str | None,
                             direct: list[Coding]) -> bool:
        if sketch.code is None or ident is None:
            return False
        if direct:
            return any(c.system == sketch.code.system and c.code == sketch.code.code
                       for c in direct)
        return self.valuesets.contains(ident, sketch.code.system, sketch.code.code)

    # -- value-position slot resolution ------------------------------------------------------------

    def _slot(self, node: dict, scope: dict):
        """Resolve an expression to a slot ref; raises UnsupportedElm when it cannot."""
        kind = node.get("type")
        if kind in ("As", "ToDateTime", "ToDate", "ToConcept", "ToQuantity",
                    "ToString", "ToDecimal", "ToInteger"):
            return self._slot(node["operand"], scope)
        if kind == "AliasRef":
            owner = scope.get(node["name"])
            if isinstance(owner, ResourceSketch):
                return _FieldSlot(self._index_of(owner), "")
            if isinstance(owner, _Closure):
                with self._in_lib(owner.lib):
                    return self._slot(owner.node, owner.scope)
            raise UnsupportedElm(f"alias {node['name']!r} unresolved in slot position")
        if kind == "Property":
            return self._property_slot(node, scope)
        if kind == "FunctionRef":
            alias = node.get("libraryName")
            target = self._lib.includes.get(alias, alias) if alias else None
            name = node["name"]
            if target == "FHIRHelpers":
                return self._slot(node["operand"][0], scope)
            lib = self.env.resolve(self._lib, alias)
            # Any user-defined function: inline its body with operand closures.
            fn = self._resolve_function(lib, name, len(node.get("operand", [])))
            bindings = {op["name"]: _Closure(arg, self._lib, dict(scope))
                        for op, arg in zip(fn["operand"], node["operand"])}
            with self._in_lib(lib):
                return self._slot(fn["expression"], bindings)
        if kind == "OperandRef":
            closure = scope.get(node["name"])
            if not isinstance(closure, _Closure):
                raise UnsupportedElm(f"operand {node['name']!r} unresolved in slot position")
            with self._in_lib(closure.lib):
                return self._slot(closure.node, closure.scope)
        if kind in ("First", "Last"):
            return self._single_slot(node["source"], scope)
        if kind == "SingletonFrom":
            return self._single_slot(node["operand"], scope)
        if kind == "ExpressionRef":
            lib = self.env.resolve(self._lib, node.get("libraryName"))
            define = lib.defines.get(node["name"])
            if define is None:
                raise SynthesizerError(f"define {node['name']!r} not in {lib.name}")
            with self._in_lib(lib):
                return self._slot(define["expression"], {})
        if kind == "Start":
            inner = self._slot(node["operand"], scope)
            return inner.start if isinstance(inner, _IntervalView) else inner
        if kind == "End":
            inner = self._slot(node["operand"], scope)
            if isinstance(inner, _IntervalView):
                if inner.mode == "open-end":
                    # No decision variable: the effective end is the type maximum by
                    # the fill invariants (no abatement, closed-null upper bound).
                    return OPEN_END
                return inner.start  # point/period: end tracks start within fill duration
            return inner
        if kind == "DateFrom":
            return self._slot(node["operand"], scope)
        if kind == "Query":
            return self._query_slot(node, scope)
        if kind == "Case":
            return self._choice_dispatch_slot(node, scope)
        if kind == "If":
            return self._choice_test_slot(node, scope)
        if kind == "Interval":
            return self._interval_slot(node, scope)
        if kind == "Coalesce":
            # First operand that denotes a slot the owning type actually carries — the
            # _ELEMENT_OWNERS gate makes `Coalesce(performed…, authoredOn…)` land on the
            # element that exists for the sketch's resource type.
            operands = node["operand"]
            failures: list[str] = []
            for op in operands if isinstance(operands, list) else [operands]:
                try:
                    return self._slot(op, scope)
                except UnsupportedElm as e:
                    failures.append(str(e))
            raise UnsupportedElm(f"no Coalesce operand denotes a slot: {failures}")
        raise UnsupportedElm(
            f"cannot resolve a decision variable from {kind!r} "
            f"(library {self._lib.name}, locator {node.get('locator')})")

    def _property_slot(self, node: dict, scope: dict):
        path = node["path"]
        source = node.get("source")
        if node.get("scope"):
            owner = scope.get(node["scope"])
            if isinstance(owner, _Closure):
                with self._in_lib(owner.lib):
                    base = self._slot(owner.node, owner.scope)
                return self._slot_field(base, path)
            if not isinstance(owner, ResourceSketch):
                raise UnsupportedElm(f"alias {node['scope']!r} unresolved in slot position")
            return self._sketch_slot(owner, path)
        if path == "value" and isinstance(source, dict) and source.get("type") == "Property":
            return self._property_slot(source, scope)
        if isinstance(source, dict) and source.get("type") == "ExpressionRef" \
                and source.get("name") == "Patient" and not source.get("libraryName"):
            return _FieldSlot(owner_index=None, prop=path)
        if isinstance(source, dict):
            base = self._slot(source, scope)
            return self._slot_field(base, path)
        raise UnsupportedElm(f"property {path!r} without source")

    def _choice_dispatch_slot(self, node: dict, scope: dict):
        """The ``Case`` a translator emits to dispatch a FHIR choice element.

        ``Procedure.performed[x]`` is ``Choice<dateTime, Period, Timing, …>``, so
        a helper that turns ``performed`` into an interval lowers to a Case that reads the SAME property once per
        declared type under a different cast, with a ``Null`` else. Every branch therefore
        denotes one decision variable, and resolving the Case to that slot is exact rather
        than a guess.

        A Case with a ``comparand``, or whose branches denote different slots, is a real
        conditional and stays unsupported — collapsing one would silently pick a branch.
        """
        if node.get("comparand") is not None:
            raise UnsupportedElm(
                f"Case with a comparand in slot position (library {self._lib.name}, "
                f"locator {node.get('locator')})")
        candidates = [item.get("then", {}) for item in node.get("caseItem", [])]
        candidates.append(node.get("else") or {})
        slots = [self._slot(branch, scope) for branch in candidates
                 if branch.get("type") not in (None, "Null")]
        if not slots:
            raise UnsupportedElm(
                f"Case has no resolvable branch (library {self._lib.name}, "
                f"locator {node.get('locator')})")
        if any(slot != slots[0] for slot in slots[1:]):
            raise UnsupportedElm(
                f"Case branches denote different slots, so it is a conditional rather "
                f"than a choice dispatch (library {self._lib.name}, "
                f"locator {node.get('locator')})")
        return slots[0]

    def _choice_test_slot(self, node: dict, scope: dict):
        """The ``if`` a helper writes to read a temporal FHIR choice element by its variant: ``if performed is
        DateTime then Interval[performed, performed] else performed as Interval<DateTime>``.

        Fill writes every temporal choice element as its DateTime variant (``performedDateTime``,
        ``effectiveDateTime``), so the outcome of the test is known: a test for DateTime takes the ``then`` branch
        and a test for an interval takes the ``else`` branch. Resolving to that branch is exact rather than a
        guess. Any other ``if`` in slot position is a real conditional and stays unsupported.
        """
        condition = node.get("condition") or {}
        if condition.get("type") != "Is":
            raise UnsupportedElm(
                f"If in slot position that does not test a type (library {self._lib.name}, "
                f"locator {node.get('locator')})")
        # The tested operand must itself be an element, so the test is a choice-variant test.
        self._slot(condition["operand"], scope)
        tested = condition.get("isTypeSpecifier") or {"type": "NamedTypeSpecifier", "name": condition.get("isType")}
        if tested.get("type") == "NamedTypeSpecifier" and tested.get("name") == _ELM_DATETIME:
            return self._slot(node["then"], scope)
        if tested.get("type") == "IntervalTypeSpecifier" \
                and (tested.get("pointType") or {}).get("name") == _ELM_DATETIME:
            return self._slot(node["else"], scope)
        raise UnsupportedElm(
            f"If tests a variant fill does not write ({tested}) (library {self._lib.name}, "
            f"locator {node.get('locator')})")

    def _interval_slot(self, node: dict, scope: dict):
        """An interval a helper builds from an element's own bounds.

        ``Interval[x, x]`` over one point element is that element as an instant. An interval whose high bound reads
        an element fill never writes (``_UNFILLED``) and is closed is open-ended from its low bound:
        ``Interval[Coalesce(onset, recordedDate), Coalesce(abatement, …)]`` is ``[onset, +inf)`` (CQL spec,
        Interval Operators: a closed null high boundary is the maximum).
        """
        low = self._slot(node["low"], scope)
        high = self._slot(node["high"], scope)
        if not isinstance(low, _FieldSlot) or low.owner_index is None:
            raise UnsupportedElm(
                f"interval with a low bound that is not an element (library {self._lib.name}, "
                f"locator {node.get('locator')})")
        if high == low and node.get("lowClosed") and node.get("highClosed"):
            return _IntervalView(start=low, mode="point")
        if isinstance(high, _FieldSlot) and high.owner_index is not None and node.get("highClosed") \
                and (self.state.resources[high.owner_index].resource_type, high.prop) in _UNFILLED:
            return _IntervalView(start=low, mode="open-end")
        raise UnsupportedElm(
            f"interval between two elements (library {self._lib.name}, locator {node.get('locator')})")

    def _slot_field(self, base, path: str):
        if isinstance(base, _FieldSlot):
            # A resource's `value` is its element (`"Most Recent HbA1c".value`, Observation.value[x]); only an
            # element's `value` is the FHIR primitive's own value, which the element slot already denotes.
            if base.prop == "" and base.owner_index is not None:
                return self._sketch_slot(self.state.resources[base.owner_index], path)
            if path == "value":
                return base
            if base.prop and base.owner_index is not None:
                # A backbone element's field (`hospitalization.dischargeDisposition`): one decision variable at the
                # dotted path, which fill places nested.
                return _FieldSlot(base.owner_index, f"{base.prop}.{path}")
        raise UnsupportedElm(f"property {path!r} over {type(base).__name__} slot")

    def _sketch_slot(self, sketch: ResourceSketch, path: str):
        index = self._index_of(sketch)
        first = path.split(".")[0]
        owners = _ELEMENT_OWNERS.get(first)
        if owners is not None and sketch.resource_type not in owners:
            raise UnsupportedElm(
                f"{sketch.resource_type} does not carry element {first!r}")
        if first == "period":
            return _IntervalView(start=_FieldSlot(index, "period"), mode="period")
        return _FieldSlot(index, first)

    def _single_slot(self, source: dict, scope: dict):
        """First/Last/singleton: single-sketch collapse — ensure exactly one qualifying
        element, then resolve against it."""
        if source.get("type") != "Query":
            branches = self._flatten(source, scope)
            site = f"single@{self._lib.name}:{source.get('locator')}"
            sketch = self._pick_and_instantiate(branches, site)
            return _FieldSlot(self._index_of(sketch), "")
        return self._query_slot(source, scope)

    def _query_slot(self, query: dict, scope: dict):
        sources = query.get("source", [])
        if len(sources) != 1:
            raise UnsupportedElm("multi-source query in slot position")
        alias = sources[0]["alias"]
        src = sources[0]["expression"]

        # Extension sub-query: the translator's lowering of a profile extension element
        # (`Patient.sex`, `ProcedureNotDone.recorded`) — a filter of `.extension` on a
        # constant url, returning its value. Every row of the query reads ONE element,
        # so the whole query denotes one `extension:<url>` field slot.
        if src.get("type") == "Property" and src["path"] == "extension":
            return self._extension_slot(query, src, scope)

        # Component sub-query: the source is a sketch's component list.
        if src.get("type") == "Property" and src["path"].split(".")[0] == "component":
            owner = scope.get(src["scope"]) if src.get("scope") else None
            if owner is None and src.get("source") is not None:
                base = self._slot(src["source"], scope)
                if isinstance(base, _FieldSlot) and base.owner_index is not None:
                    owner = self.state.resources[base.owner_index]
            if not isinstance(owner, ResourceSketch):
                raise UnsupportedElm("component query over an unresolved owner")
            code = self._component_code(query.get("where"))
            return _ComponentSlot(self._index_of(owner),
                                  code=code.code, system=code.system)

        branches = self._flatten(src, scope)
        site = f"query-slot@{self._lib.name}:{query.get('locator')}"
        sketch = self._pick_and_instantiate(branches, site)
        # Satisfy the query's own where against THE single sketch; data-dependent
        # comparisons resolving into the same sketch are auto-true under the collapse.
        if query.get("where") is not None:
            self._satisfy(query["where"], True, {**scope, alias: sketch})
        ret = query.get("return")
        if ret is None:
            return _FieldSlot(self._index_of(sketch), "")
        return self._slot(ret["expression"], {**scope, alias: sketch})

    def _extension_slot(self, query: dict, src: dict, scope: dict):
        """The ``extension:<url>`` field slot an extension sub-query denotes."""
        where = query.get("where")
        url = None

        def find_url(node):
            nonlocal url
            if isinstance(node, dict):
                if node.get("type") == "Literal" and isinstance(node.get("value"), str) \
                        and node["value"].startswith("http"):
                    url = node["value"]
                    return
                for value in node.values():
                    find_url(value)
            elif isinstance(node, list):
                for item in node:
                    find_url(item)

        find_url(where)
        if url is None:
            raise UnsupportedElm("extension query without a constant url filter")
        prop = f"extension:{url}"
        if src.get("scope"):
            owner = scope.get(src["scope"])
            if isinstance(owner, _Closure):
                with self._in_lib(owner.lib):
                    base = self._slot(owner.node, owner.scope)
                if isinstance(base, _FieldSlot) and base.prop == "":
                    return _FieldSlot(base.owner_index, prop)
                raise UnsupportedElm("extension query over an unresolved closure owner")
            if not isinstance(owner, ResourceSketch):
                raise UnsupportedElm(f"extension query owner {src['scope']!r} unresolved")
            return _FieldSlot(self._index_of(owner), prop)
        source = src.get("source")
        if isinstance(source, dict) and source.get("type") == "ExpressionRef" \
                and source.get("name") == "Patient" and not source.get("libraryName"):
            return _FieldSlot(owner_index=None, prop=prop)
        if isinstance(source, dict) and source.get("type") == "AliasRef":
            owner = scope.get(source["name"])
            if isinstance(owner, ResourceSketch):
                return _FieldSlot(self._index_of(owner), prop)
        raise UnsupportedElm("extension query over an unresolved owner")

    def _pick_and_instantiate(self, branches, site: str) -> ResourceSketch:
        chosen: list[ResourceSketch] = []

        def attempt(branch):
            chosen.clear()
            chosen.append(self._instantiate(branch, site))

        self._try_routes(site, branches, attempt)
        return chosen[0]

    def _component_code(self, where: dict | None) -> Code:
        """The component discriminator: the CodeRef inside `component.code ~ X`."""
        def find(node):
            if isinstance(node, dict):
                if node.get("type") == "CodeRef":
                    return node
                for v in node.values():
                    hit = find(v)
                    if hit is not None:
                        return hit
            elif isinstance(node, list):
                for x in node:
                    hit = find(x)
                    if hit is not None:
                        return hit
            return None

        ref = find(where)
        if ref is None:
            raise UnsupportedElm("component query without a CodeRef discriminator")
        code = self._const_eval(ref)
        if not isinstance(code, Code):
            raise UnsupportedElm("component discriminator is not a Code")
        return code

    # -- comparisons ------------------------------------------------------------------------------

    def _satisfy_Equal(self, node: dict, want, scope) -> None:
        self._coded_compare(node, want, scope)

    def _satisfy_Equivalent(self, node: dict, want, scope) -> None:
        self._coded_compare(node, want, scope)

    def _coded_compare(self, node: dict, want, scope) -> None:
        left, right = node["operand"]
        try:
            expected = self._const_eval(right)
        except (UnsupportedElm, SynthesizerError) as e:
            raise UnsupportedElm(
                f"comparison against a non-constant (library {self._lib.name}, "
                f"locator {node.get('locator')}): {e}") from e
        slot = self._slot(left, scope)
        if isinstance(slot, _IntervalView):
            raise UnsupportedElm("equality over an interval slot")
        site = f"eq:{getattr(slot, 'prop', slot)}@{node.get('locator')}"
        if isinstance(expected, (dt.date, dt.datetime)):
            # `end of <interval of X> = maximum DateTime` and friends: a temporal
            # equality against a constant is a (degenerate) window constraint.
            boundary = _as_datetime(expected)
            if slot is OPEN_END:
                # The effective end IS the DateTime maximum by the fill invariants.
                is_max = boundary == dt.datetime(9999, 12, 31, 23, 59, 59, 999000)
                if (want is True) != is_max:
                    raise Unsatisfiable(f"open-ended slot equality at {site}")
                return
            if want is True:
                self._narrow(slot, boundary, boundary, site)
            elif want is False:
                self._escape(slot, boundary, boundary, site)
            else:
                self._assign(slot, ABSENT, site)
            return
        if isinstance(expected, Concept):
            if not expected.codes:
                raise UnsupportedElm("equivalence against an empty concept")
            expected = expected.codes[0]
        if not isinstance(expected, (Code, str)) and expected is not None:
            raise UnsupportedElm(f"equality against {type(expected).__name__}")
        if want is True:
            self._assign(slot, expected, site)
        elif want is False:
            self._assign(slot, self._alternative(slot, [expected], site), site)
        else:
            self._assign(slot, ABSENT, site)

    def _satisfy_In(self, node: dict, want, scope) -> None:
        point_node, container_node = node["operand"]
        if (age := self._age_call(point_node, scope)) is not None:
            self._satisfy_age(age, container_node, want, node)
            return
        site = f"in@{self._lib.name}:{node.get('locator')}"
        try:
            container = self._const_eval(container_node)
        except (UnsupportedElm, SynthesizerError):
            # Data-dependent window (`recorded during day of Encounter.period`):
            # correlate the two slots directly (SYNTH-PDS-CORRELATED).
            left = self._slot(point_node, scope)
            right = self._slot(container_node, scope)
            self._pin_together(left, right, want, site)
            return
        slot = self._slot(point_node, scope)
        if isinstance(container, list):
            if not all(isinstance(x, str) for x in container):
                raise UnsupportedElm("In over a non-string list")
            if want is True:
                self._assign(slot, container[self.chooser.pick(site, len(container))], site)
            elif want is False:
                self._assign(slot, self._alternative(slot, container, site), site)
            else:
                self._assign(slot, ABSENT, site)
            return
        if isinstance(container, CqlInterval):
            self._temporal_constraint(slot, container, want, site)
            return
        raise UnsupportedElm(f"In over {type(container).__name__}")

    def _satisfy_InValueSet(self, node: dict, want, scope) -> None:
        """A coded element tested against a value set (`notDoneReason in "Medical
        Reason"`): membership demanded true picks an expansion member for the slot;
        false/null leaves the element absent (InValueSet(null) is false)."""
        vs = node["valueset"]
        vs_lib = self.env.resolve(self._lib, vs.get("libraryName"))
        url = vs_lib.valuesets.get(vs["name"])
        if url is None:
            raise SynthesizerError(f"value set {vs['name']!r} not in {vs_lib.name}")
        slot = self._slot(node["code"], scope)
        site = f"in-vs:{getattr(slot, 'prop', slot)}@{node.get('locator')}"
        if want is True:
            member = self._pick_member(url, site)
            self._assign(slot, Code(system=member.system, code=member.code,
                                    display=member.display), site)
        else:
            self._assign(slot, ABSENT, site)

    def _satisfy_AnyInValueSet(self, node: dict, want, scope) -> None:
        """A coded LIST element tested against a value set (`reasonCode in "Overweight
        or Obese"`). The codes expression is the translator's per-element coercion
        query; the underlying list element is the decision variable."""
        vs = node["valueset"]
        vs_lib = self.env.resolve(self._lib, vs.get("libraryName"))
        url = vs_lib.valuesets.get(vs["name"])
        if url is None:
            raise SynthesizerError(f"value set {vs['name']!r} not in {vs_lib.name}")
        slot = self._coded_list_slot(node["codes"], scope)
        site = f"any-in-vs:{slot.prop}@{node.get('locator')}"
        if want is not True:
            self._assign(slot, ABSENT, site)
            return
        member = self._pick_member(url, site)
        code = Code(system=member.system, code=member.code, display=member.display)
        fields, prop, _ = self._fields_of(slot)
        current = fields.get(prop)
        if current is ABSENT:
            raise Unsatisfiable(f"{prop} demanded both absent and populated",
                                conflicts=[site])
        if current is None:
            fields[prop] = [code]
        elif isinstance(current, list):
            if code not in current:
                fields[prop] = [*current, code]
        else:
            raise Unsatisfiable(f"{prop} holds {current!r}, not a coded list",
                                conflicts=[site])

    def _coded_list_slot(self, node: dict, scope: dict) -> _FieldSlot:
        """The list-element field a codes expression reads: the first scoped Property
        under the coercion query, resolved against the bound alias."""
        def find(current):
            if isinstance(current, dict):
                if current.get("type") == "Property" and current.get("scope") \
                        and current["scope"] != "$this":
                    return current
                for value in current.values():
                    hit = find(value)
                    if hit is not None:
                        return hit
            elif isinstance(current, list):
                for item in current:
                    hit = find(item)
                    if hit is not None:
                        return hit
            return None

        prop_node = find(node)
        if prop_node is None:
            raise UnsupportedElm("AnyInValueSet over an unrecognized codes expression")
        owner = scope.get(prop_node["scope"])
        if isinstance(owner, _Closure):
            with self._in_lib(owner.lib):
                base = self._slot(owner.node, owner.scope)
            if isinstance(base, _FieldSlot) and base.prop == "" \
                    and base.owner_index is not None:
                owner = self.state.resources[base.owner_index]
        if not isinstance(owner, ResourceSketch):
            raise UnsupportedElm(
                f"AnyInValueSet owner {prop_node['scope']!r} unresolved")
        return _FieldSlot(self._index_of(owner),
                          prop_node["path"].split(".")[0])

    def _satisfy_IncludedIn(self, node: dict, want, scope) -> None:
        inner, outer_node = node["operand"]
        outer = self._const_eval(outer_node)
        if not isinstance(outer, CqlInterval):
            raise UnsupportedElm("IncludedIn over a non-constant outer interval")
        slot = self._slot(inner, scope)
        self._temporal_constraint(slot, outer, want,
                                  f"included-in@{self._lib.name}:{node.get('locator')}")

    def _satisfy_Overlaps(self, node: dict, want, scope) -> None:
        left, right = node["operand"]
        window = self._const_eval(right)
        if not isinstance(window, CqlInterval):
            raise UnsupportedElm("Overlaps against a non-constant window")
        slot = self._slot(left, scope)
        site = f"overlaps@{self._lib.name}:{node.get('locator')}"
        hi = _as_datetime(window.effective_high())
        if isinstance(slot, _IntervalView) and slot.mode == "open-end":
            # [start, +inf) overlaps [lo, hi] iff start <= hi.
            if want is True:
                self._narrow(slot.start, None, hi - _MARGIN, site)
            elif want is False:
                self._narrow(slot.start, hi + _MARGIN, None, site)
            else:
                self._assign(slot.start, ABSENT, site)
            return
        self._temporal_constraint(slot, window, want, site)

    def _point_vs_const(self, node: dict, want, scope, *, before: bool,
                        strict: bool) -> None:
        left, right = node["operand"]
        site = f"cmp@{self._lib.name}:{node.get('locator')}"
        try:
            boundary = self._const_eval(right)
        except (UnsupportedElm, SynthesizerError):
            self._point_vs_slot(node, want, scope, before=before, strict=strict,
                                site=site)
            return
        boundary = _as_datetime(self._point_of(boundary, low=not before))
        slot = self._slot(left, scope)
        if slot is OPEN_END:
            # `prevalence ends before X` with no abatement: the effective end is the
            # type maximum, so the relation is decided, not solved.
            result = not before  # +infinity is after every constant boundary
            if want is None or (want is not result):
                raise Unsatisfiable(f"open-ended boundary relation fixed at {site}")
            return
        start = slot.start if isinstance(slot, _IntervalView) else slot
        if want is None:
            self._assign(start, ABSENT, site)
            return
        toward = before if want else not before
        if toward:
            self._narrow(start, None, boundary - _MARGIN, site)
        else:
            self._narrow(start, boundary + _MARGIN, None, site)

    def _point_vs_slot(self, node: dict, want, scope, *, before: bool, strict: bool,
                       site: str) -> None:
        """A one-sided temporal comparison whose boundary is another resource's data
        (`<a condition's active interval> starts before or on day of X.authoredOn`): pin the
        boundary slot to an instant, then place the left side against it. Same-instant
        placement is only sound when the effective relation is non-strict."""
        left, right = node["operand"]
        right_slot = self._slot(right, scope)
        if right_slot is OPEN_END:
            raise UnsupportedElm("comparison against an open-ended boundary slot")
        left_slot = self._slot(left, scope)
        if left_slot is OPEN_END:
            result = not before
            if want is None or (want is not result):
                raise Unsatisfiable(f"open-ended boundary relation fixed at {site}")
            return
        left_start = left_slot.start if isinstance(left_slot, _IntervalView) else left_slot
        if want is None:
            self._assign(left_start, ABSENT, site)
            return
        right_start = right_slot.start if isinstance(right_slot, _IntervalView) else right_slot
        point = self._pin_instant(right_start, f"{site}:anchor")
        toward = before if want else not before
        # want False inverts strictness: not(a < b) is a >= b, not(a <= b) is a > b.
        effective_strict = strict if want else not strict
        pad = 2 * _MARGIN if effective_strict else dt.timedelta(0)
        if toward:
            self._narrow(left_start, None, point - pad, site)
        else:
            self._narrow(left_start, point + pad, None, site)

    @staticmethod
    def _point_of(value, *, low: bool):
        if isinstance(value, CqlInterval):
            return value.effective_low() if low else value.effective_high()
        return value

    def _satisfy_Before(self, node, want, scope) -> None:
        self._point_vs_const(node, want, scope, before=True, strict=True)

    def _satisfy_SameOrBefore(self, node, want, scope) -> None:
        self._point_vs_const(node, want, scope, before=True, strict=False)

    def _satisfy_After(self, node, want, scope) -> None:
        self._point_vs_const(node, want, scope, before=False, strict=True)

    def _satisfy_SameOrAfter(self, node, want, scope) -> None:
        self._point_vs_const(node, want, scope, before=False, strict=False)

    def _satisfy_SameAs(self, node: dict, want, scope) -> None:
        left, right = node["operand"]
        site = f"same-as@{self._lib.name}:{node.get('locator')}"
        try:
            boundary = self._const_eval(right)
        except (UnsupportedElm, SynthesizerError):
            boundary = None
        if boundary is None:
            # Data-dependent: same-sketch relations hold by the fill invariants;
            # cross-sketch ones pin the two slots together (SYNTH-PDS-CORRELATED).
            left_slot = self._slot(left, scope)
            right_slot = self._slot(right, scope)
            self._pin_together(left_slot, right_slot, want, site)
            return
        boundary = _as_datetime(boundary)
        slot = self._slot(left, scope)
        start = slot.start if isinstance(slot, _IntervalView) else slot
        if want is True:
            self._narrow(start, boundary, boundary, site)
        elif want is False:
            self._escape(start, boundary, boundary, site)
        else:
            self._assign(start, ABSENT, site)

    def _numeric_compare(self, node: dict, want, scope, *, less: bool) -> None:
        left, right = node["operand"]
        if (age := self._age_call(left, scope)) is not None:
            self._age_bound(age, right, want, node, less=less,
                            or_equal=node["type"] in ("LessOrEqual", "GreaterOrEqual"))
            return
        threshold = self._const_eval(right)
        if isinstance(threshold, (int, float)):
            threshold = Quantity(float(threshold), "1")
        if not isinstance(threshold, Quantity):
            raise UnsupportedElm(f"numeric comparison against {type(threshold).__name__}")
        slot = self._slot(left, scope)
        if isinstance(slot, _ComponentSlot):
            domain = self._component_domain(slot, threshold.unit)
        elif isinstance(slot, _FieldSlot) and slot.owner_index is not None:
            domain = self._field_numeric_domain(slot, threshold.unit)
        else:
            raise UnsupportedElm(f"numeric comparison over {type(slot).__name__}")
        site = f"num@{self._lib.name}:{node.get('locator')}"
        if want is None:
            raise UnsupportedElm("null numeric comparison in solve position")
        # Falsification inverts strictness: not(a < t) is a >= t, not(a <= t) is a > t.
        strict = node["type"] in ("Less", "Greater")
        effective_strict = strict if want else not strict
        eps = 1.0 if effective_strict else 0.0
        toward_less = less if want else not less
        if toward_less:
            domain.narrow(None, threshold.value - eps, site)
        else:
            domain.narrow(threshold.value + eps, None, site)

    def _satisfy_Less(self, node, want, scope) -> None:
        self._numeric_compare(node, want, scope, less=True)

    def _satisfy_LessOrEqual(self, node, want, scope) -> None:
        self._numeric_compare(node, want, scope, less=True)

    def _satisfy_Greater(self, node, want, scope) -> None:
        self._numeric_compare(node, want, scope, less=False)

    def _satisfy_GreaterOrEqual(self, node, want, scope) -> None:
        self._numeric_compare(node, want, scope, less=False)

    def _component_domain(self, slot: _ComponentSlot, unit: str) -> NumericDomain:
        sketch = self.state.resources[slot.owner_index]
        key = f"component:{slot.code}"
        current = sketch.fields.get(key)
        if current is None or current is PRESENT:
            current = NumericDomain(lo=0.0, hi=400.0, unit=unit)
            sketch.fields[key] = current
            sketch.fields.setdefault(f"component-system:{slot.code}", slot.system)
        if not isinstance(current, NumericDomain):
            raise Unsatisfiable(f"component {slot.code} demanded both value and absence")
        return current

    def _field_numeric_domain(self, slot: _FieldSlot, unit: str) -> NumericDomain:
        """A compared plain quantity element (``Observation.value``); PRESENT (an
        earlier ``is not null``) refines into a domain."""
        fields, prop, _ = self._fields_of(slot)
        current = fields.get(prop)
        if current is None or current is PRESENT:
            current = NumericDomain(lo=0.0, hi=400.0, unit=unit)
            fields[prop] = current
        if not isinstance(current, NumericDomain):
            raise Unsatisfiable(f"{prop} demanded both a numeric value and {current!r}")
        return current

    # -- age ---------------------------------------------------------------------------------------

    def _asof_range(self, node: dict, scope: dict) -> tuple[dt.date, dt.date]:
        """The dates a CalculateAgeAt asOf expression can take: a constant collapses to
        itself; a data slot (`age at start of QualifyingEncounter.period`) contributes
        its whole temporal domain, so a birthDate bound derived from BOTH ends is
        conservative — sound wherever inside the domain the element lands."""
        try:
            as_of = self._const_eval(node)
        except (UnsupportedElm, SynthesizerError):
            slot = self._slot(node, scope)
            if slot is OPEN_END:
                raise UnsupportedElm("age at an open-ended boundary")
            domain = self._temporal_domain_of(slot)
            return domain.lo.date(), domain.hi.date()
        if isinstance(as_of, dt.datetime):
            as_of = as_of.date()
        if not isinstance(as_of, dt.date):
            raise UnsupportedElm("CalculateAgeAt asOf is not a date")
        return as_of, as_of

    def _narrow_birth_band(self, as_of_lo: dt.date, as_of_hi: dt.date,
                           lo_age: int, hi_age: int, site: str) -> None:
        """birthDate bounds keeping the age inside [lo_age, hi_age] at EVERY instant of
        [as_of_lo, as_of_hi]: the intersection of the per-instant dob ranges."""
        dob_lo, _ = _dob_range(as_of_hi, lo_age, hi_age)
        _, dob_hi = _dob_range(as_of_lo, lo_age, hi_age)
        if dob_lo > dob_hi:
            raise Unsatisfiable(f"age band unreachable across the asOf window at {site}")
        self._narrow_birth_date(dob_lo, dob_hi, site=site)

    def _age_call(self, node: dict, scope: dict) -> tuple[dt.date, dt.date] | None:
        """The asOf dates of the age a node denotes, or None when it is not an age: a ``CalculateAgeAt`` written in
        place, or a define that is one (``Common."Age At End Of Period"``), followed through each reference into
        the library it is written in."""
        lib = self._lib
        while node.get("type") == "ExpressionRef":
            lib = self.env.resolve(lib, node.get("libraryName"))
            define = lib.defines.get(node["name"])
            if define is None:
                raise SynthesizerError(f"define {node['name']!r} not in {lib.name}")
            node, scope = define["expression"], {}
        if node.get("type") != "CalculateAgeAt":
            return None
        if node.get("precision") != "Year":
            raise UnsupportedElm(f"CalculateAgeAt precision {node.get('precision')!r}")
        with self._in_lib(lib):
            return self._asof_range(node["operand"][1], scope)

    def _age_bound(self, as_of: tuple[dt.date, dt.date], threshold_node: dict, want, node: dict, *,
                   less: bool, or_equal: bool) -> None:
        """A one-sided age comparison lowered to a birthDate bound."""
        as_of_lo, as_of_hi = as_of
        threshold = self._const_eval(threshold_node)
        if not isinstance(threshold, int):
            raise UnsupportedElm("age compared against a non-integer")
        site = f"age-cmp@{self._lib.name}:{node.get('locator')}"
        if want is None:
            self._set_birth_date_absent(site)
            return
        # Reduce op/want to a demanded closed age band.
        if less:
            band = (0, threshold - (0 if or_equal else 1)) if want \
                else (threshold + (1 if or_equal else 0), 120)
        else:
            band = (threshold + (0 if or_equal else 1), 120) if want \
                else (0, threshold - (1 if or_equal else 0))
        if band[0] > band[1]:
            raise Unsatisfiable(f"empty age band at {site}")
        self._narrow_birth_band(as_of_lo, as_of_hi, band[0], band[1], site)

    def _satisfy_age(self, as_of: tuple[dt.date, dt.date], interval_node: dict, want, node: dict) -> None:
        as_of_lo, as_of_hi = as_of
        interval = self._const_eval(interval_node)
        if not isinstance(interval, CqlInterval) or not isinstance(interval.effective_low(), int):
            raise UnsupportedElm("age comparison against a non-integer interval")
        lo_age, hi_age = interval.effective_low(), interval.effective_high()
        site = f"age@{self._lib.name}:{node.get('locator')}"
        if want is None:
            self._set_birth_date_absent(site)
            return
        if want is True:
            self._narrow_birth_band(as_of_lo, as_of_hi, lo_age, hi_age, site)
            return

        def younger(_) -> None:
            if lo_age < 1:
                raise Unsatisfiable("no age below the band", conflicts=[site])
            self._narrow_birth_band(as_of_lo, as_of_hi, max(0, lo_age - 5), lo_age - 1, site)

        def older(_) -> None:
            self._narrow_birth_band(as_of_lo, as_of_hi, hi_age + 1, hi_age + 15, site)

        self._try_routes(f"age-out@{site}", [younger, older], lambda route: route(None))

    # -- constraint application --------------------------------------------------------------------

    def _fields_of(self, slot) -> tuple[dict, str, str]:
        if isinstance(slot, _FieldSlot):
            if slot.owner_index is None:
                return self.state.patient_fields, slot.prop, "Patient"
            sketch = self.state.resources[slot.owner_index]
            return sketch.fields, slot.prop, sketch.resource_type
        raise UnsupportedElm(f"assignment to {type(slot).__name__}")

    def _assign(self, slot, value, site: str) -> None:
        if isinstance(slot, _FieldSlot) and slot.owner_index is None \
                and slot.prop == "birthDate" and value is ABSENT:
            self._set_birth_date_absent(site)
            return
        fields, prop, _ = self._fields_of(slot)
        current = fields.get(prop)
        if current is None:
            fields[prop] = value
            return
        if isinstance(current, (TemporalDomain, DateDomain, NumericDomain)):
            raise Unsatisfiable(f"{prop} demanded both a domain and {value!r}", conflicts=[site])
        same = (current is ABSENT and value is ABSENT) or \
               (current is not ABSENT and value is not ABSENT and current == value)
        if not same:
            raise Unsatisfiable(f"{prop} demanded both {current!r} and {value!r}",
                                conflicts=[site])

    def _alternative(self, slot, avoid: list, site: str):
        _, prop, resource_type = self._fields_of(slot)
        alternative = _ALTERNATIVES.get((resource_type, prop))
        if alternative is None or alternative in avoid:
            raise UnsupportedElm(
                f"no falsifying alternative for {resource_type}.{prop} avoiding {avoid} "
                f"(extend solve._ALTERNATIVES)")
        return alternative

    def _temporal_domain_of(self, slot) -> TemporalDomain:
        """The slot's temporal domain, created at the clamp bounds on first touch. A
        PRESENT marker (an earlier ``is not null``) is refined into a domain."""
        if isinstance(slot, _IntervalView):
            slot = slot.start
        fields, prop, _ = self._fields_of(slot)
        current = fields.get(prop)
        if current is ABSENT:
            raise Unsatisfiable(f"{prop} demanded both absent and bounded")
        if current is None or current is PRESENT:
            current = TemporalDomain(lo=self.clamp_lo, hi=self.clamp_hi)
            fields[prop] = current
        if not isinstance(current, TemporalDomain):
            raise Unsatisfiable(f"{prop} holds {current!r}, not a temporal domain")
        return current

    def _narrow(self, slot, lo: dt.datetime | None, hi: dt.datetime | None, site: str) -> None:
        if isinstance(slot, _IntervalView):
            slot = slot.start
        if isinstance(slot, _FieldSlot) and slot.owner_index is None \
                and slot.prop == "birthDate":
            self._narrow_birth_date(lo.date() if lo else dt.date(1900, 1, 1),
                                    hi.date() if hi else dt.date(2100, 1, 1), site=site)
            return
        self._temporal_domain_of(slot).narrow(lo, hi, site)

    def _pin_instant(self, slot, site: str) -> dt.datetime:
        """Collapse a temporal slot to one chooser-drawn instant inside its domain and
        return it (SYNTH-PDS-CORRELATED). Minute-granular, so fill reproduces it
        exactly; the domain's own margins keep it >= 1 day inside every window that
        constrained it."""
        domain = self._temporal_domain_of(slot)
        span = (domain.hi - domain.lo).total_seconds()
        offset = int(self.chooser.unit(site) * span) // 60 * 60
        point = domain.lo + dt.timedelta(seconds=offset)
        domain.narrow(point, point, site)
        return point

    def _pin_together(self, left, right, want, site: str) -> None:
        """A temporal relation whose both sides are data: pin the right side to one
        instant, then place the left relative to it. Same-instant placement satisfies
        every non-strict/day-precision relation the corpus states; ``want`` False
        escapes the left side by >= 2 days so no day-truncation can un-falsify it."""
        left_start = left.start if isinstance(left, _IntervalView) else left
        right_start = right.start if isinstance(right, _IntervalView) else right
        if isinstance(left_start, _FieldSlot) and isinstance(right_start, _FieldSlot) \
                and left_start.owner_index is not None \
                and left_start.owner_index == right_start.owner_index:
            if want is True:
                return  # one sketch's own elements relate to themselves (fill invariant)
            raise Unsatisfiable(f"self-referential relation cannot be falsified at {site}")
        if want is None:
            self._assign(left_start, ABSENT, site)
            return
        point = self._pin_instant(right_start, f"{site}:anchor")
        if want is True:
            self._narrow(left_start, point, point, site)
        else:
            self._escape(left_start, point - _DAY, point + _DAY, site)

    def _temporal_constraint(self, slot, window: CqlInterval, want, site: str) -> None:
        start = slot.start if isinstance(slot, _IntervalView) else slot
        mode = slot.mode if isinstance(slot, _IntervalView) else "point"
        lo = _as_datetime(window.effective_low())
        hi = _as_datetime(window.effective_high())
        pad = 2 * _MARGIN if mode == "period" else _MARGIN
        if want is True:
            self._narrow(start, lo + _MARGIN, hi - pad, site)
        elif want is False:
            self._escape(start, lo, hi, site)
        else:
            self._assign(start, ABSENT, site)

    def _escape(self, slot, lo: dt.datetime, hi: dt.datetime, site: str) -> None:
        def before(_) -> None:
            try:
                bound = lo - _MARGIN
            except OverflowError:
                raise Unsatisfiable(f"no room before the type minimum at {site}") from None
            self._narrow(slot, None, bound, f"{site}:before")

        def after(_) -> None:
            try:
                bound = hi + _MARGIN
            except OverflowError:
                # Escaping past the DateTime maximum (an `= maximum DateTime` guard):
                # only the before side exists.
                raise Unsatisfiable(f"no room after the type maximum at {site}") from None
            self._narrow(slot, bound, None, f"{site}:after")

        self._try_routes(f"escape@{site}", [before, after], lambda route: route(None))

    def _narrow_birth_date(self, lo: dt.date, hi: dt.date, *, site: str) -> None:
        current = self.state.birth_date
        if current is ABSENT:
            raise Unsatisfiable("birthDate demanded both absent and bounded", conflicts=[site])
        if current is None:
            current = DateDomain(lo=dt.date(1900, 1, 1), hi=dt.date(2100, 1, 1))
            self.state.birth_date = current
        current.narrow(lo, hi, site)

    def _set_birth_date_absent(self, site: str) -> None:
        if isinstance(self.state.birth_date, DateDomain):
            raise Unsatisfiable("birthDate demanded both bounded and absent", conflicts=[site])
        self.state.birth_date = ABSENT


def _as_datetime(value):
    if isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day)
    raise UnsupportedElm(f"expected a temporal boundary, got {type(value).__name__}")


def _shift_years(day: dt.date, years: int) -> dt.date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:
        return day.replace(year=day.year + years, day=28)


def _dob_range(as_of: dt.date, lo_age: int, hi_age: int) -> tuple[dt.date, dt.date]:
    return _shift_years(as_of, -(hi_age + 1)) + _DAY, _shift_years(as_of, -lo_age)
