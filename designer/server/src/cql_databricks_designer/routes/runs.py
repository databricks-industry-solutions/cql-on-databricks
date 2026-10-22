"""RUN-CLIENT-MIGRATION, RUN-CLIENT-REQUEST-REOPEN — Designer runs and current reads as the caller.

The ``draft`` wire field is a presentation/cache label. The resultSet JSON body carries calculation
request context for recompilation and tag discovery. RUN-CLIENT-PUBLISHED (amended 2026-10-10): the
Designer reads only the caller's private results; it never reads Job-published results.
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

from cql_databricks.compiler.resolve import ResolutionError, resolve_data_source
from cql_databricks.data_bindings import ContentResolver, StatementObserver
from cql_databricks_designer.data_bindings import user_content_resolver
from cql_databricks.result import (ResultsUnavailable, StepRecord, ensure_private_destination,
                                     find_private_destination, materialize, open_result_set,
                                     prepare_calculation)
from cql_databricks.result.insights.evaluation import MetricsUnavailable
from cql_databricks.result.insights.flow import GateRequest, cohort_flow as build_cohort_flow
from cql_databricks.result.insights.metrics import Cohort, measure_metrics as build_measure_metrics
from cql_databricks.result.insights.profile import define_profile as build_define_profile
from cql_databricks.result.insights.reads import read_metrics as build_read_metrics
from cql_databricks.result.selection_reading import (SelectionClosure, open_selection, selection_address,
                                                       selection_counts)
from cql_databricks_designer.dto import CohortFlowRequest, MeasureMetricsRequest, ReadMetricsRequest, RunRequest
from cql_databricks_designer.run_log import RunContext, RunLogEntry, RunRecorder

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CalculationSettings:
    """RUN-CLIENT-PRIVATE-PROVISIONING — deployment catalog and bounded interactive storage."""
    private_catalog: str
    runtime_catalog: str
    runtime_schema: str = "runtime"
    capacity: int = 10_000


@dataclass
class CalculationEnv:
    """SEC-OBO — capture request transports before a stream enters a worker thread.

    APP-COMPUTE-TARGET — the transports take the chosen warehouse ID, or None for the deployment's.
    ``warehouses`` lists the warehouses the signed-in user can see, plus that default.
    APP-RUN-LOG — ``user_run`` also takes a statement observer; ``run_context`` resolves who runs and on
    which warehouse, on the request thread; ``run_log`` hands a finished run to the background writer.
    """
    user_execute: Callable[[str | None], Callable]
    user_run: Callable[[str | None, StatementObserver | None], Callable]
    settings: Callable[[], CalculationSettings]
    run_context: Callable[[str | None], RunContext]
    run_log: Callable[[RunLogEntry], None]
    warehouses: Callable[[], dict] = lambda: {"default": None, "warehouses": []}
    #: CLIENT-DISCOVERY-SCOPE — the deployment's catalog scopes, over the signed-in user's runner.
    content: Callable[[Callable], ContentResolver] = user_content_resolver


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
    data_source_id: str = Field(alias="dataSourceId")
    period_start: str | None = Field(default=None, alias="periodStart")
    period_end: str | None = Field(default=None, alias="periodEnd")
    warehouse_id: str | None = Field(default=None, alias="warehouseId")


class ResultReadRequest(BaseModel):
    """RUN-CLIENT-REQUEST-REOPEN — the calculation request travels in JSON, never the URL."""
    model_config = {"populate_by_name": True, "extra": "forbid"}
    result_set: dict = Field(alias="resultSet")
    library: str = ""
    version: str = ""
    data_source_id: str = Field(alias="dataSourceId")
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


def _session(instance: str, library: str, version: str, population: str, period) -> str:
    """RUN-CLIENT-RESULT-SETS — scope the logical session; CQL edits replace its selection."""
    if not instance.strip():
        raise ValueError("a logical session instance is required")
    year = date.today().year
    scope = [instance, library, version, population, period[0] or f"{year}-01-01",
             period[1] or f"{year}-12-31"]
    return hashlib.sha256(json.dumps(scope, separators=(",", ":")).encode()).hexdigest()


def _handle(instance: str, selection_id: str) -> str:
    return "durable." + base64.urlsafe_b64encode(json.dumps([instance, selection_id]).encode()).decode()


class TaskProgress:
    """RUN-PROGRESS-TASKS — the run's planned definitions and the state of each, as ``executing`` frames.

    ``planned`` is the client's ``on_plan`` and ``step`` its ``on_step``; each sends one frame through ``emit``.
    A reused or computed step is ``completed`` and a failed one ``failed``. The first definition not yet
    reported is ``running``, unless one failed, because then nothing more runs.
    """

    def __init__(self, emit: Callable[[dict], None]) -> None:
        self._emit = emit
        self._keys: tuple[str, ...] = ()
        self._states: dict[str, str] = {}

    def planned(self, keys: tuple[str, ...]) -> None:
        self._keys = keys
        self._emit(self.frame())

    def step(self, step: StepRecord) -> None:
        self._states[step.definition] = "failed" if step.error else "completed"
        self._emit(self.frame())

    def frame(self) -> dict:
        failed = "failed" in self._states.values()
        running = None if failed else next((k for k in self._keys if k not in self._states), None)
        tasks = [{"taskKey": key, "status": self._states.get(key) or ("running" if key == running else "pending")}
                 for key in self._keys]
        return {"stage": "executing", "status": "running", "tasks": tasks}


_REFUSED = (ResolutionError, ResultsUnavailable, MetricsUnavailable, ValueError, PermissionError)


def router(*, compiler, env: CalculationEnv) -> APIRouter:
    """RUN-CLIENT-MIGRATION — one durable path for execution and result derivations."""
    router = APIRouter()

    @dataclass
    class Acting:
        settings: CalculationSettings
        execute: Callable
        run: Callable
        content: ContentResolver

    def acting(warehouse: str | None, observer: StatementObserver | None = None) -> Acting:
        run = env.user_run(warehouse, observer)
        return Acting(env.settings(), env.user_execute(warehouse), run, env.content(run))

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
            data_source=req.data_source_id, period=period, cql=req.cql,
            runtime_catalog=a.settings.runtime_catalog, runtime_schema=a.settings.runtime_schema,
            targets=tuple(req.defines) if req.defines is not None else None)
        recorder.compiled(prepared.library, prepared.version, compile_started)
        session = _session(instance, prepared.library, prepared.version, prepared.population, period)
        private = ensure_private_destination(a.settings.private_catalog, run=a.run)
        yield {"stage": "executing", "status": "running", "message": "Computing or reusing completed results"}
        results = materialize(prepared, *prepared.targets, run=a.run, destination=private,
                              session_id=session, capacity=a.settings.capacity,
                              mode="recompute" if req.recompute else "skip_if_computed",
                              computed_since=req.computed_since, on_plan=progress.planned,
                              on_step=lambda step: (recorder.step(step), progress.step(step)))
        yield {"stage": "done", "status": "completed", **completed(results, prepared, instance, a)}

    def completed(results, prepared, instance, a) -> dict:
        """RUN-CLIENT-REQUEST-REOPEN — one payload for a finished run and a reopened selection."""
        handle = _handle(instance, results.selection.selection_id)
        return {"identity": {"library": prepared.library, "version": prepared.version,
                             "dataSource": prepared.population, "draft": handle},
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
            if not req.data_source_id:
                raise ValueError("data_source_id is required — select a Data Source.")
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
        """APP-RUN-LOG — the recorder observes every statement of the run's own runner."""
        try:
            overrides = req.overrides or {}
            recorder = RunRecorder(context=env.run_context(req.warehouse_id), data_source=req.data_source_id,
                                   period=(overrides.get("periodStart"), overrides.get("periodEnd")),
                                   trigger=req.trigger, mode="recompute" if req.recompute else "skip_if_computed")
            return acting(req.warehouse_id, recorder.observe), recorder, None
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

    def evaluation(a, name, version, handle, data_source, period, address):
        results = open_selection(address, run=a.run, content=a.content, compiler=compiler)
        request = results.request
        if not request or list(period) != request["period"]:
            raise ResultsUnavailable("complete selection does not match the requested measurement period")
        closure = SelectionClosure(results, root=name, draft=handle)
        if closure.data_source != data_source or closure.libraries[name].scope.version != version:
            raise ResultsUnavailable("complete selection does not match the requested library and population")
        return results, closure

    def options(a, name, version, handle, data_source, period, address):
        results, closure = evaluation(a, name, version, handle, data_source, period, address)
        return dict(library=name, version=version, data_source_id=data_source,
                    period_start=period[0], period_end=period[1], eval_id=results.selection.selection_id,
                    draft=handle, closure=closure, results_catalog=results.selection.destination[0],
                    execute=a.execute)

    def patient_function(a, source):
        return resolve_data_source(a.content, a.run, source, compiler.resource_types(),
                                   include_change_logs=False).bindings.get("Patient")

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

        Never computes, never creates the private schema, never reads another user's or a Job's results
        (RUN-CLIENT-PUBLISHED, amended 2026-10-10). A miss is an answer (``found: false``), not an error.
        """
        try:
            a = acting(req.warehouse_id)
            period = (req.period_start, req.period_end)
            prepared = prepare_calculation(content=a.content, run=a.run, compiler=compiler,
                data_source=req.data_source_id, period=period, cql=req.cql,
                runtime_catalog=a.settings.runtime_catalog, runtime_schema=a.settings.runtime_schema)
            private = find_private_destination(a.settings.private_catalog, run=a.run)
            if private is None:
                return {"found": False, "reason": "nothing computed yet"}
            instance = x_designer_instance or ""
            session = _session(instance, prepared.library, prepared.version, prepared.population, period)
            results = open_result_set(prepared, *prepared.targets, run=a.run, destination=private,
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
        if not (req.data_source_id and req.library and req.version):
            return JSONResponse(status_code=400, content={"error": "data_source, library and version are required"})
        try:
            a = acting(req.warehouse_id)
            results, _ = evaluation(a, req.library, req.version, req.draft, req.data_source_id,
                                    (req.period_start, req.period_end), req.result_set)
            return counts(results, req.library, req.draft, a.execute)
        except _REFUSED as error:
            return refused(error)

    @router.post("/api/libraries/{name}/{version}/defines/{define}/profile")
    def define_profile(name: str, version: str, define: str, req: ResultReadRequest):
        try:
            a = acting(req.warehouse_id)
            opts = options(a, name, version, req.draft, req.data_source_id,
                           (req.period_start, req.period_end), req.result_set)
            return build_define_profile(**opts, define=define, define_library=req.define_library or name,
                                         patient_function=patient_function(a, req.data_source_id)).to_dict()
        except _REFUSED as error:
            return refused(error)

    @router.post("/api/libraries/{name}/{version}/metrics")
    def measure_metrics(name: str, version: str, req: MetricsBody):
        try:
            a = acting(req.warehouse_id)
            opts = options(a, name, version, req.draft, req.data_source_id, (req.period_start, req.period_end), req.result_set)
            return build_measure_metrics(**opts, defines=[(d.library, d.name, d.resultType) for d in req.defines],
                                          cohorts={(d.library, d.name): _cohort(d.cohort) for d in req.defines},
                                          patient_function=patient_function(a, req.data_source_id)).to_dict()
        except _REFUSED as error:
            return refused(error)

    @router.post("/api/libraries/{name}/{version}/reads")
    def read_metrics(name: str, version: str, req: ReadsBody):
        try:
            a = acting(req.warehouse_id)
            opts = options(a, name, version, req.draft, req.data_source_id, (req.period_start, req.period_end), req.result_set)
            return build_read_metrics(**opts, reads=[r.model_dump() for r in req.reads])
        except _REFUSED as error:
            return refused(error)

    @router.post("/api/libraries/{name}/{version}/flow")
    def cohort_flow(name: str, version: str, req: FlowBody):
        try:
            a = acting(req.warehouse_id)
            opts = options(a, name, version, req.draft, req.data_source_id, (req.period_start, req.period_end), req.result_set)
            return build_cohort_flow(**opts, gates=[GateRequest(
                stage=g.stage, stage_library=g.stage_library, base=g.base, base_library=g.base_library,
                polarity=g.polarity, criteria=[(c.library, c.name) for c in g.criteria]) for g in req.gates],
                patient_function=patient_function(a, req.data_source_id)).to_dict()
        except _REFUSED as error:
            return refused(error)

    return router
