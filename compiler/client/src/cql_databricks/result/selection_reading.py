"""RUN-CLIENT-RESULT-SETS, PIPE-RESULT-DERIVATIONS — shared readers over one resolved current selection."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from cql_databricks.resolvers.interfaces import Resolvers

from .calculation import prepare_calculation
from .durable import MaterializedResults, ResultsUnavailable, open_result_set
from .insights.evaluation import ClosureLibrary, SelectedLibrary
from .results import Execute, PinnedRelation, count_relations, relation_columns


def selection_address(results: MaterializedResults) -> dict:
    """RUN-CLIENT-RESULT-SETS — portable locator for one complete selection, never independent results."""
    selection = results.selection
    return {"destination": list(selection.destination),
            "session_id": selection.session_id, "selection_id": selection.selection_id,
            "request_hash": selection.request_hash,
            "targets": list(selection.targets),
            "computed_since": selection.computed_since.isoformat() if selection.computed_since else None,
            **({"calculation": results.request} if results.request is not None else {})}


def open_selection(address: dict, *, run, resolvers: Resolvers, query=None, content=None, compiler=None,
                   files=None) -> MaterializedResults:
    """RUN-CLIENT-REQUEST-REOPEN — recompile the request, then discover tags as the reader.

    ``resolvers`` are the reader's (`CLIENT-RESOLVERS`): their data and terminology recompile the request, and their
    result resolver, whose destination the address must name, finds the results. Supply a query/prepared
    calculation, or content/compiler transports and a calculation request in the address. The address carries no
    compiled plan, secrets or server-side session key. A request from a folder location (RUN-ADDRESS-LOCATION) is
    replayed through ``files``.
    """
    required = {"destination", "session_id", "selection_id", "request_hash", "targets", "computed_since"}
    if (not isinstance(address, dict) or not required <= address.keys()
            or address.keys() - required - {"calculation"}):
        raise ValueError("a complete result-set address is required")
    destination = address["destination"]
    if (not isinstance(destination, (list, tuple)) or len(destination) != 2
            or any(not isinstance(part, str) or not part.strip() for part in destination)
            or any(not isinstance(address[key], str) or not address[key].strip()
                   for key in ("session_id", "selection_id", "request_hash"))
            or not isinstance(address["targets"], list) or not address["targets"]
            or any(not isinstance(t, str) or not t.strip() for t in address["targets"])
            or len(set(address["targets"])) != len(address["targets"])):
        raise ValueError("invalid complete result-set address")
    if resolvers.results is None:
        raise ValueError("reopening results needs a result resolver (CLIENT-RESULT-RESOLVER)")
    if tuple(destination) != tuple(resolvers.results.destination):
        raise ValueError(f"the address names results in {'.'.join(destination)}, and this result resolver's are in "
                         f"{'.'.join(resolvers.results.destination)}")
    cutoff = datetime.fromisoformat(address["computed_since"]) if address["computed_since"] is not None else None
    request = address.get("calculation")
    if query is None:
        if not isinstance(request, dict) or content is None or compiler is None:
            raise ValueError("reopening requires the calculation request and compiler/content transports")
        allowed = {"environment", "runtime_catalog", "runtime_schema", "library", "version", "cql", "period",
                   "targets", "location"}
        if request.keys() - allowed:
            raise ValueError("unsupported calculation request fields")
        fields = {k: v for k, v in request.items() if k != "environment"}
        query = prepare_calculation(content=content, compiler=compiler, run=run, files=files, resolvers=resolvers,
                                    **fields)
    results = open_result_set(query, *address["targets"], results=resolvers.results, run=run,
                              session_id=address["session_id"], computed_since=cutoff)
    if results.selection.request_hash != address["request_hash"]:
        raise ResultsUnavailable("the calculation request changed; recompute before reopening results")
    if results.selection.selection_id != address["selection_id"]:
        raise ResultsUnavailable("the complete selection was replaced; select its new address explicitly")
    return results


@dataclass(frozen=True)
class TableDefine:
    """One define a selected library has a table for. ``read`` is a materialized retrieve's descriptor."""
    mv_name: str
    read: dict | None = None


@dataclass(frozen=True)
class SelectedOutput:
    """RUN-CLIENT-RESULT-SETS — a named output, schema and completion evidence from one selection."""
    results: MaterializedResults
    name: str

    def relation(self) -> str:
        """RUN-CLIENT-OPTIMISTIC — validate local closure descriptors before a current-table read."""
        self.results.selection.validate(self.results._backend)
        return f"({self.results.sql(self.name)})"

    @property
    def completed(self):
        return self.results.results[self.name].result

    @property
    def columns(self) -> list[tuple[str, str]]:
        return [tuple(column) for column in json.loads(self.completed.artifact.schema_ddl)]

    @property
    def origin(self) -> dict:
        key = self.results.selection.targets[self.name]
        return json.loads(next(d.origin_json for d in self.results.selection.definitions if d.key == key))


class SelectionClosure:
    """RUN-CLIENT-RESULT-SETS — adapt compiler metadata, never discover a closure from latest tables.

    Pass the selection ID as the derivation's eval_id and the calculation's environment name as data_source_id. The draft
    argument is an explicit presentation label, not a storage address or an authorization credential.
    """

    def __init__(self, results: MaterializedResults, *, root: str, draft: str = "") -> None:
        self.results = results
        self.root = root
        self.draft = draft
        #: CLIENT-ENVIRONMENT: the environment the calculation ran in, which the derivations key their closure by.
        self.environment = (results.request or {}).get("environment")
        self.libraries: dict[str, ClosureLibrary] = {}
        outcomes = {outcome.definition: outcome for outcome in results.selection.outcomes}
        for definition in results.selection.definitions:
            origin = json.loads(definition.origin_json)
            library, version, name = origin["library"], origin["version"], origin["define"]
            descriptor = origin.get("retrieve_read")
            if not library or (not name and descriptor is None):
                raise ValueError("selected compiler metadata has no library/define identity")
            if descriptor is not None:
                name = definition.key
            entry = self.libraries.setdefault(library, ClosureLibrary(
                SelectedLibrary(library, version, draft=draft if library == root else ""), relations={}))
            if entry.scope.version != version or name in entry.relations:
                raise ValueError("ambiguous library/define in complete selection")
            entry.relations[name] = PinnedRelation(outcomes[definition.key].result.artifact,
                                                   results.selection.selection_id, results._backend.usable)
            entry.defines.append(TableDefine(name, {"resourceType": descriptor["resource_type"],
                                                    "profile": descriptor["profile"]} if descriptor else None))
        if root not in self.libraries:
            raise ValueError("root library is not in the complete selection")

    def closure(self, data_source_id: str, eval_id: str, *, root: str, draft: str) -> dict[str, ClosureLibrary]:
        """RUN-CLIENT-RESULT-SETS — refuse a different scope/session instead of selecting current records."""
        if (data_source_id != self.environment or eval_id != self.results.selection.selection_id
                or root != self.root or draft != self.draft):
            raise ValueError("derivation request does not match the complete selection")
        self.results.selection.validate(self.results._backend)
        return self.libraries


def selection_counts(results: MaterializedResults, *, execute: Execute) -> dict[str, tuple[int, int | None]]:
    """RUN-CLIENT-RESULT-SETS, SPEC-METRIC — count named outputs from the selected current tables.

    Successful empty results are computed zeros, not missing work. No catalog scans, SDP naming,
    source change logs or independent latest-completion lookups participate in this read.
    """
    results.selection.validate(results._backend)
    refs = {name: PinnedRelation(outcome.result.artifact, results.selection.selection_id, results._backend.usable)
            for name, outcome in results.results.items()}
    columns = relation_columns(results.selection.destination[0], refs.values())
    counts = count_relations(execute, columns, eval_id=results.selection.selection_id)
    return {name: counts[ref] for name, ref in refs.items()}
