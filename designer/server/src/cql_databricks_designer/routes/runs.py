"""RUN-CLIENT-MIGRATION, RUN-CLIENT-REQUEST-REOPEN — Designer runs and current reads as the caller.

The ``draft`` wire field is a presentation/cache label. The resultSet JSON body carries calculation
request context for recompilation and tag discovery. DOC-ENVIRONMENT-PICKER, RUN-CLIENT-DRAFT-RESULTS: every run is
in the environment the Designer has picked (None is the default), reads its data and terminology with the open
folder's libraries over its knowledge, and writes its ``draft_results`` as the user. It reuses only the caller's own
results (`RUN-CLIENT-RESULT-OWNERSHIP`).
APP-COMPUTE-TARGET: every request may name the SQL warehouse it runs on; none means the deployment's.
APP-RUN-LOG: every Refresh and Automatic run, completed or failed, is handed to the background run log
before its last frame is sent.
RUN-PROGRESS-TASKS: ``/api/run-stream`` sends the run's definitions and their states as each one finishes.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
from collections.abc import Callable, Iterator
from contextvars import copy_context
from dataclasses import dataclass
from datetime import date, datetime, timezone
from queue import SimpleQueue
from threading import Thread
from typing import Any, Literal

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from cql_databricks.compiler.environment import DATA, Environment, EnvironmentLookupError, EnvironmentResolver
from cql_databricks.compiler.libraries import LibraryFiles
from cql_databricks.compiler.resolve import ResolutionError
from cql_databricks.data_bindings import ContentResolver, StatementObserver, WarehouseSession
from cql_databricks_designer.data_bindings import user_workspace_files
from cql_databricks.result import (ResultsUnavailable, StepRecord, materialize, open_result_set,
                                     prepare_calculation)
from cql_databricks.result.insights.evaluation import MetricsUnavailable
from cql_databricks.result.insights.flow import GateRequest, cohort_flow as build_cohort_flow
from cql_databricks.result.insights.metrics import Cohort, measure_metrics as build_measure_metrics
from cql_databricks.result.insights.profile import define_profile as build_define_profile
from cql_databricks.result.insights.reads import read_metrics as build_read_metrics
from cql_databricks.result.insights.score import selection_score
from cql_databricks.result.selection_reading import (SelectionClosure, open_selection, selection_address,
                                                       selection_counts)
from cql_databricks_designer.dto import CohortFlowRequest, MeasureMetricsRequest, ReadMetricsRequest, RunRequest
from cql_databricks_designer.run_log import RunContext, RunLogEntry, RunRecorder

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CalculationSettings:
    """SVC-CATALOG-LAYOUT, RUN-CLIENT-DRAFT-RESULTS — the runtime schema every environment's runs use, and bounded
    interactive storage."""
    runtime_catalog: str
    runtime_schema: str = "runtime"
    capacity: int = 10_000


@dataclass
class CalculationEnv:
    """SEC-OBO — capture request transports before a stream enters a worker thread.

    APP-COMPUTE-TARGET — the transports take the chosen warehouse ID, or None for the deployment's.
    ``warehouses`` lists the warehouses the signed-in user can see, plus that default.
    RUN-CLIENT-SESSION-REUSE — ``user_session`` is the signed-in user's held warehouse session, built on the
    request thread (SEC-OBO). The App keeps it open across runs and requests, so no route closes it.
    APP-RUN-LOG — a run observes its own statements through ``session.observed``; ``run_context`` resolves who
    runs and on which warehouse, on the request thread; ``run_log`` hands a finished run to the background writer.
    """
    user_session: Callable[[str | None], WarehouseSession]
    settings: Callable[[], CalculationSettings]
    run_context: Callable[[str | None], RunContext]
    run_log: Callable[[RunLogEntry], None]
    warehouses: Callable[[], dict] = lambda: {"default": None, "warehouses": []}
    #: RUN-ADDRESS-LOCATION — reads a folder location as the signed-in user; built on the request thread.
    files: Callable[[], LibraryFiles] = user_workspace_files


class CalculationRequest(RunRequest):
    """RUN-CLIENT-MIGRATION — do not silently accept legacy modes, subject limits or data_as_of."""
    model_config = {"populate_by_name": True, "extra": "forbid"}
    #: APP-COMPUTE-TARGET — the SQL warehouse to run on; None means the deployment's.
    warehouse_id: str | None = None
    #: APP-RUN-LOG — the Designer action that started the run. Recorded, never acted on.
    trigger: Literal["refresh", "automatic"]


class ReopenRequest(BaseModel):
    """RUN-CLIENT-REQUEST-REOPEN — the displayed calculation, to reopen read-only. CQL travels in JSON."""
    model_config = {"populate_by_name": True, "extra": "forbid"}
    cql: str
    #: RUN-ADDRESS-LOCATION — where the library's includes resolve first; None is the environment's knowledge.
    location: str | None = None
    #: DOC-ENVIRONMENT-PICKER — the environment the calculation ran in; None is the default.
    environment: str | None = None
    period_start: str | None = Field(default=None, alias="periodStart")
    period_end: str | None = Field(default=None, alias="periodEnd")
    warehouse_id: str | None = Field(default=None, alias="warehouseId")


class ResultReadRequest(BaseModel):
    """RUN-CLIENT-REQUEST-REOPEN — the calculation request travels in JSON, never the URL."""
    model_config = {"populate_by_name": True, "extra": "forbid"}
    result_set: dict = Field(alias="resultSet")
    library: str = ""
    version: str = ""
    #: DOC-ENVIRONMENT-PICKER — the environment the calculation ran in; None is the default.
    environment: str | None = None
    period_start: str | None = Field(default=None, alias="periodStart")
    period_end: str | None = Field(default=None, alias="periodEnd")
    draft: str = ""
    define_library: str | None = Field(default=None, alias="defineLibrary")
    warehouse_id: str | None = Field(default=None, alias="warehouseId")


class MetricsBody(MeasureMetricsRequest):
    """APP-COMPUTE-TARGET — a metrics read on the chosen warehouse."""
    warehouse_id: str | None = Field(default=None, alias="warehouseId")


class ReadsBody(ReadMetricsRequest):
    """APP-COMPUTE-TARGET — a read-metrics read on the chosen warehouse."""
    warehouse_id: str | None = Field(default=None, alias="warehouseId")


class FlowBody(CohortFlowRequest):
    """APP-COMPUTE-TARGET — a cohort-flow read on the chosen warehouse."""
    warehouse_id: str | None = Field(default=None, alias="warehouseId")


def _cohort(dto) -> Cohort:
    """SPEC-POPULATION-CASCADE — the wire cohort as the counting module takes it."""
    return Cohort(all_of=tuple((r.library, r.name) for r in dto.all_of),
                  none_of=tuple((r.library, r.name) for r in dto.none_of))


def _session(instance: str, library: str, version: str, population: str, period,
             location: str | None = None) -> str:
    """RUN-CLIENT-RESULT-SETS — scope the logical session; CQL edits replace its selection.

    RUN-ADDRESS-LOCATION — the location is part of the scope, so a folder's copy of a library and a store's copy of
    the same name and version keep separate selections in one tab."""
    if not instance.strip():
        raise ValueError("a logical session instance is required")
    year = date.today().year
    scope = [instance, library, version, population, period[0] or f"{year}-01-01",
             period[1] or f"{year}-12-31"] + ([location] if location else [])
    return hashlib.sha256(json.dumps(scope, separators=(",", ":")).encode()).hexdigest()


def _handle(instance: str, selection_id: str) -> str:
    return "durable." + base64.urlsafe_b64encode(json.dumps([instance, selection_id]).encode()).decode()


class TaskProgress:
    """RUN-PROGRESS-TASKS — the run's planned definitions and the state of each, as ``executing`` frames.

    ``planned`` is the client's ``on_plan``, ``started`` its ``on_start`` and ``step`` its ``on_step``; each sends
    one frame through ``emit``. A started definition is ``running`` until its step arrives (RUN-CLIENT-PARALLEL-DEFINES:
    several may run at once). A reused or computed step is ``completed``, a failed one ``failed`` and one cancelled
    because another failed ``skipped``.
    """

    def __init__(self, emit: Callable[[dict], None]) -> None:
        self._emit = emit
        self._keys: tuple[str, ...] = ()
        self._states: dict[str, str] = {}

    def planned(self, keys: tuple[str, ...]) -> None:
        self._keys = keys
        self._emit(self.frame())

    def started(self, key: str) -> None:
        self._states[key] = "running"
        self._emit(self.frame())

    def step(self, step: StepRecord) -> None:
        self._states[step.definition] = ("skipped" if step.decision == "cancelled" else
                                         "failed" if step.error else "completed")
        self._emit(self.frame())

    def frame(self) -> dict:
        tasks = [{"taskKey": key, "status": self._states.get(key, "pending")} for key in self._keys]
        return {"stage": "executing", "status": "running", "tasks": tasks}


_REFUSED = (ResolutionError, ResultsUnavailable, MetricsUnavailable, ValueError, PermissionError,
            EnvironmentLookupError)


def router(*, compiler, env: CalculationEnv) -> APIRouter:
    """RUN-CLIENT-MIGRATION — one durable path for execution and result derivations."""
    router = APIRouter()

    @dataclass
    class Acting:
        settings: CalculationSettings
        execute: Callable
        run: Callable
        environment: Environment
        content: ContentResolver
        files: LibraryFiles

        @property
        def patient(self) -> str:
            """BIND-PUBLISHED-VIEW — the environment's Patient view, which the demographics and totals read."""
            return f"{self.environment.schema(DATA)}.patient"

    def acting(warehouse: str | None, environment: str | None,
               observer: StatementObserver | None = None) -> Acting:
        """RUN-CLIENT-SESSION-REUSE — the request's runner and ``Execute`` share the user's held session.

        DOC-ENVIRONMENT-PICKER — the environment is found as the user (`CLIENT-ENVIRONMENT-RESOLVER`), and its content
        is read as the user too."""
        run = env.user_session(warehouse).observed(observer)
        environments = EnvironmentResolver(run)
        picked = environments.resolve(environment)
        return Acting(env.settings(), run.rows, run, picked, environments.content(picked), env.files())

    def counts(results, root, handle, execute):
        counted = selection_counts(results, execute=execute)
        origins = {d.key: json.loads(d.origin_json) for d in results.selection.definitions}
        defines = []
        for name, key in results.selection.targets.items():
            origin = origins[key]
            if origin["library"] == root:
                subjects, elements = counted[name]
                defines.append({"name": origin["define"], "define": origin["define"], "subjects": subjects,
                                "elements": elements, "status": "computed", "computed": True})
        return {"schema": ".".join(results.selection.destination), "libraryName": root,
                "version": next(o["version"] for o in origins.values() if o["library"] == root),
                "versionKind": "private",
                "draft": handle, "defines": defines}

    def run_events(req: RunRequest, a: Acting, instance: str, recorder: RunRecorder,
                   progress: TaskProgress) -> Iterator[dict]:
        overrides = req.overrides or {}
        unknown = set(overrides) - {"periodStart", "periodEnd"}
        if unknown:
            raise ValueError(f"unsupported calculation overrides: {sorted(unknown)}")
        period = (overrides.get("periodStart"), overrides.get("periodEnd"))
        yield {"stage": "compiling", "message": "Resolving and compiling the CQL"}
        compile_started = datetime.now(timezone.utc)
        prepared = prepare_calculation(content=a.content, run=a.run, compiler=compiler,
            environment=a.environment, period=period, cql=req.cql,
            runtime_catalog=a.settings.runtime_catalog, runtime_schema=a.settings.runtime_schema,
            targets=tuple(req.defines) if req.defines is not None else None,
            location=req.location, files=a.files)
        recorder.compiled(prepared.library, prepared.version, compile_started, a.environment.name)
        session = _session(instance, prepared.library, prepared.version, prepared.population, period, req.location)
        yield {"stage": "executing", "status": "running", "message": "Computing or reusing completed results"}
        # RUN-CLIENT-DRAFT-RESULTS: a Designer run writes the environment's draft_results, as the user.
        results = materialize(prepared, *prepared.targets, run=a.run, environment=a.environment, purpose="private",
                              session_id=session, capacity=a.settings.capacity,
                              mode="recompute" if req.recompute else "skip_if_computed",
                              computed_since=req.computed_since, on_plan=progress.planned, on_start=progress.started,
                              on_step=lambda step: (recorder.step(step), progress.step(step)))
        yield {"stage": "done", "status": "completed", **completed(results, prepared, instance, a)}

    def completed(results, prepared, instance, a) -> dict:
        """RUN-CLIENT-REQUEST-REOPEN — one payload for a finished run and a reopened selection."""
        handle = _handle(instance, results.selection.selection_id)
        return {"identity": {"library": prepared.library, "version": prepared.version,
                             "environment": prepared.population, "draft": handle},
                "resultSet": selection_address(results),
                "results": counts(results, prepared.library, handle, a.execute),
                "outcomes": [{"definition": o.definition, "decision": o.decision, "reason": o.reason,
                              "completedAt": o.result.completed_at.isoformat()} for o in results.outcomes]}

    def logged(recorder: RunRecorder, error: str | None) -> None:
        """APP-RUN-LOG, DEC-RUN-LOG-FAILURE — hand the run to the background log; never alter the run."""
        try:
            env.run_log(recorder.finish(error))
        except Exception:
            log.exception("APP-RUN-LOG: run %s was not logged", recorder.run_id)

    def guarded(req, a, instance, recorder: RunRecorder, emit: Callable[[dict], None]):
        """APP-RUN-LOG — the run is logged before its last frame, so a closed stream cannot lose the row.

        RUN-PROGRESS-TASKS — ``emit`` receives the progress frames sent while the definitions run."""
        try:
            for event in run_events(req, a, instance, recorder, TaskProgress(emit)):
                if event["stage"] == "done":
                    logged(recorder, None)
                    event = {**event, "runId": recorder.run_id}
                yield event
        except Exception as error:  # SQL/transport failures must also finish the stream with an error frame.
            log.exception("durable calculation failed")
            message = f"{type(error).__name__}: {error}"
            logged(recorder, message)
            yield {"stage": "error", "error": message, "runId": recorder.run_id}

    def acting_or_refusal(req: CalculationRequest):
        """APP-RUN-LOG — the recorder observes every statement of the run's own runner.

        RUN-CLIENT-SESSION-REUSE — that runner is the user's held session, taken here on the request thread
        (SEC-OBO), so the run's statements run warm after the user's first."""
        try:
            overrides = req.overrides or {}
            recorder = RunRecorder(context=env.run_context(req.warehouse_id), environment=req.environment,
                                   period=(overrides.get("periodStart"), overrides.get("periodEnd")),
                                   trigger=req.trigger, mode="recompute" if req.recompute else "skip_if_computed")
            return acting(req.warehouse_id, req.environment, recorder.observe), recorder, None
        except Exception as error:
            return None, None, JSONResponse(status_code=503, content={"ok": False, "error": str(error)})

    def streamed(req, a, instance, recorder: RunRecorder) -> Iterator[dict]:
        """RUN-PROGRESS-TASKS — the run goes on a worker thread, so its progress frames leave while it runs.

        One queue carries every frame in the order the run produced it. The worker carries the request's
        context and runs to the end even if the stream closes, so the run is still logged (APP-RUN-LOG)."""
        frames: SimpleQueue = SimpleQueue()

        def work() -> None:
            try:
                for event in guarded(req, a, instance, recorder, frames.put):
                    frames.put(event)
            finally:
                frames.put(None)

        Thread(target=copy_context().run, args=(work,), daemon=True, name="designer-run").start()
        while (event := frames.get()) is not None:
            yield event

    @router.post("/api/run-stream")
    def run_measure_stream(req: CalculationRequest, x_designer_instance: str | None = Header(default=None)):
        a, recorder, refusal = acting_or_refusal(req)
        if refusal is not None:
            return refusal
        events = streamed(req, a, req.session_id or x_designer_instance or "", recorder)
        return StreamingResponse((f"data: {json.dumps(event)}\n\n" for event in events),
                                 media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @router.post("/api/run")
    def run_measure(req: CalculationRequest, x_designer_instance: str | None = Header(default=None)):
        a, recorder, refusal = acting_or_refusal(req)
        if refusal is not None:
            return refusal
        last = list(guarded(req, a, req.session_id or x_designer_instance or "", recorder, lambda frame: None))[-1]
        if last.get("stage") != "done":
            return JSONResponse(status_code=400, content={"ok": False, "error": last["error"]})
        return {"ok": True, "identity": last["identity"], "draft": last["identity"]["draft"],
                "results": last["results"], "outcomes": last["outcomes"], "resultSet": last["resultSet"]}

    def evaluation(a, name, version, handle, period, address):
        results = open_selection(address, run=a.run, content=a.content, compiler=compiler, files=a.files)
        request = results.request
        if not request or list(period) != request["period"]:
            raise ResultsUnavailable("complete selection does not match the requested measurement period")
        closure = SelectionClosure(results, root=name, draft=handle)
        if closure.environment != a.environment.name or closure.libraries[name].scope.version != version:
            raise ResultsUnavailable("complete selection does not match the requested library and environment")
        return results, closure

    def options(a, name, version, handle, period, address):
        results, closure = evaluation(a, name, version, handle, period, address)
        return dict(library=name, version=version, data_source_id=a.environment.name,
                    period_start=period[0], period_end=period[1], eval_id=results.selection.selection_id,
                    draft=handle, closure=closure, results_catalog=results.selection.destination[0],
                    execute=a.execute)

    def refused(error):
        return JSONResponse(status_code=409, content={"error": str(error)})

    def failed(error):
        # A transport/SQL failure (for example, no CAN_USE on the chosen warehouse) is shown, never retried
        # on another warehouse.
        log.exception("Designer result read failed")
        return JSONResponse(status_code=502, content={"error": f"{type(error).__name__}: {error}"})

    @router.get("/api/warehouses")
    def list_warehouses():
        """APP-COMPUTE-TARGET — the SQL warehouses the signed-in user can see, and the deployment default."""
        try:
            return env.warehouses()
        except Exception as error:
            return failed(error)

    @router.post("/api/reopen")
    def reopen(req: ReopenRequest, x_designer_instance: str | None = Header(default=None)):
        """RUN-CLIENT-REQUEST-REOPEN — the caller's own results for exactly this calculation, read-only.

        Never computes and never reads another identity's results (`RUN-CLIENT-RESULT-OWNERSHIP`): it reads the
        caller's own, in the environment's ``draft_results`` and then its ``results``. A miss is an answer
        (``found: false``), not an error.
        """
        try:
            a = acting(req.warehouse_id, req.environment)
            period = (req.period_start, req.period_end)
            prepared = prepare_calculation(content=a.content, run=a.run, compiler=compiler,
                environment=a.environment, period=period, cql=req.cql,
                runtime_catalog=a.settings.runtime_catalog, runtime_schema=a.settings.runtime_schema,
                location=req.location, files=a.files)
            instance = x_designer_instance or ""
            session = _session(instance, prepared.library, prepared.version, prepared.population, period,
                               req.location)
            results = open_result_set(prepared, *prepared.targets, run=a.run, environment=a.environment,
                                      session_id=session, purpose="private")
            return {"found": True, **completed(results, prepared, instance, a)}
        except ResultsUnavailable as error:
            return {"found": False, "reason": str(error)}
        except _REFUSED as error:
            return refused(error)
        except Exception as error:
            return failed(error)

    @router.post("/api/results")
    def get_results(req: ResultReadRequest) -> Any:
        if not (req.library and req.version):
            return JSONResponse(status_code=400, content={"error": "library and version are required"})
        try:
            a = acting(req.warehouse_id, req.environment)
            results, _ = evaluation(a, req.library, req.version, req.draft,
                                    (req.period_start, req.period_end), req.result_set)
            return counts(results, req.library, req.draft, a.execute)
        except _REFUSED as error:
            return refused(error)

    @router.post("/api/libraries/{name}/{version}/defines/{define}/profile")
    def define_profile(name: str, version: str, define: str, req: ResultReadRequest):
        try:
            a = acting(req.warehouse_id, req.environment)
            opts = options(a, name, version, req.draft, (req.period_start, req.period_end), req.result_set)
            return build_define_profile(**opts, define=define, define_library=req.define_library or name,
                                         patient_relation=a.patient).to_dict()
        except _REFUSED as error:
            return refused(error)

    @router.post("/api/libraries/{name}/{version}/metrics")
    def measure_metrics(name: str, version: str, req: MetricsBody):
        try:
            a = acting(req.warehouse_id, req.environment)
            opts = options(a, name, version, req.draft, (req.period_start, req.period_end), req.result_set)
            metrics = build_measure_metrics(**opts, defines=[(d.library, d.name, d.resultType) for d in req.defines],
                                            cohorts={(d.library, d.name): _cohort(d.cohort) for d in req.defines},
                                            patient_relation=a.patient).to_dict()
            # RUN-CLIENT-MEASURE-SCORE — the score is the client's, over the root library's populations.
            declared = list(opts["closure"].libraries[name].relations) if name in opts["closure"].libraries else []
            score = selection_score(**opts, scoring=req.scoring, declared=declared) if req.scoring else None
            return {**metrics, "score": score.to_dict() if score else None}
        except _REFUSED as error:
            return refused(error)

    @router.post("/api/libraries/{name}/{version}/reads")
    def read_metrics(name: str, version: str, req: ReadsBody):
        try:
            a = acting(req.warehouse_id, req.environment)
            opts = options(a, name, version, req.draft, (req.period_start, req.period_end), req.result_set)
            return build_read_metrics(**opts, reads=[r.model_dump() for r in req.reads])
        except _REFUSED as error:
            return refused(error)

    @router.post("/api/libraries/{name}/{version}/flow")
    def cohort_flow(name: str, version: str, req: FlowBody):
        try:
            a = acting(req.warehouse_id, req.environment)
            opts = options(a, name, version, req.draft, (req.period_start, req.period_end), req.result_set)
            return build_cohort_flow(**opts, gates=[GateRequest(
                stage=g.stage, stage_library=g.stage_library, base=g.base, base_library=g.base_library,
                polarity=g.polarity, criteria=[(c.library, c.name) for c in g.criteria]) for g in req.gates],
                patient_relation=a.patient).to_dict()
        except _REFUSED as error:
            return refused(error)

    return router
