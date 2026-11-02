"""RUN-CLIENT-MIGRATION, BIND-PUBLISHED-VIEW, CLIENT-RESOLVER-IDENTITY — shared preparation in an environment,
without legacy result or change-log reads."""
from __future__ import annotations

from dataclasses import replace

import pytest

from cql_databricks.resolvers.environment import Environment, EnvironmentResolver
from cql_databricks.compiler.providers import PipelinePlan
from cql_databricks.resolvers.interfaces import ValueSetRef
from cql_databricks.data_bindings import Corpus, Library, TaggedSchema
from cql_databricks.result.calculation import prepare_calculation
from test_durable_plan import plan


ROOT = "library Synthetic version '1'\ninclude Helper version '1' called H\ndefine Output: H.Base"
INCLUDE = "library Helper version '1'\ndefine Base: 7"


class Content:
    def corpus(self):
        schema = TaggedSchema("synthetic", "synthetic", "synthetic", "knowledge")
        libraries = [Library(name=name, version="1", url=f"http://synthetic/{name}", fhir_version="1",
                             source=source, description="", title="", kind="library", model="",
                             data_requirements=[], knowledge=schema)
                     for name, source in (("Synthetic", ROOT), ("Helper", INCLUDE))]
        return Corpus(lambda *args: [], [schema], libraries)

    generation = 1

    def identity(self):
        return "synthetic.knowledge"


ENVIRONMENT = Environment(name="synthetic-population", catalog="synthetic", version="2")


class Compiler:
    def __init__(self):
        self.requests = []
        self.described = []
        self.translations = []
        compiled = plan()
        self.plan = PipelinePlan([replace(compiled.datasets[0], library="Helper"), compiled.datasets[1]],
                                 compiled.compiler_version)

    def resource_types(self):
        raise AssertionError("SVC-COMPILE-BATCH must not fetch resource types separately")

    def resource_schemas(self, types):
        raise AssertionError("SVC-COMPILE-BATCH: a table relation needs no reshape")

    def describe(self, request):
        raise AssertionError("BIND-CALL-SITE: an environment binds by rule, so a run asks no describe")

    def translate_result(self, source, includes):
        raise AssertionError("SVC-COMPILE-BATCH must not translate libraries separately")

    def compile_pipeline(self, request):
        self.requests.append(request)
        return self.plan


def run(statement, params=None):
    raise AssertionError(f"BIND-PUBLISHED-VIEW: a run in an environment reads no catalog, but sent {statement}")


def prepare(compiler, **changes):
    runner = changes.get("run", run)
    resolvers = EnvironmentResolver(runner).resolvers(ENVIRONMENT)
    return prepare_calculation(**(dict(content=Content(), run=run, compiler=compiler, cql=ROOT, resolvers=resolvers,
        runtime_catalog="synthetic_runtime", period=("2026-01-01", "2026-12-31")) | changes))


def test_preparation_binds_actual_addresses_typed_period_and_include_closure_once():
    """RUN-CLIENT-RESULT-IDENTITY — compiler contracts supply targets and identity; no SQL string patches."""
    compiler = Compiler()
    prepared = prepare(compiler)
    request = compiler.requests[0]
    assert request.durable is True
    assert request.cql == ROOT and request.elm == ""
    assert request.parameters == {}
    assert request.measurement_period == ("2026-01-01", "2026-12-31")
    # BIND-PUBLISHED-VIEW: the environment's data and knowledge views, by rule, with their identities.
    assert request.data == {"rule": {"schema": "synthetic.data", "identity": "synthetic.data"}}
    assert request.terminology == {"rule": [{"table": "synthetic.knowledge.valueset",
                                              "identity": "synthetic.knowledge"}]}
    assert request.udf_schema == "synthetic_runtime.runtime"
    assert len(request.deps) == 1 and request.deps[0].name == "Helper"
    assert request.deps[0].cql == INCLUDE and not request.deps[0].elm
    assert compiler.translations == []
    assert prepared.targets == ("Helper.Base", "Synthetic.Output")
    assert prepared.compile_pipeline(durable=True) is compiler.plan
    # SVC-COMPILE-BATCH: one compile and no describe; no value set, so no digest query.
    assert len(compiler.requests) == 1 and compiler.described == []
    # RUN-CLIENT-DRAFT-RESULTS: CQL text is never the environment's own content.
    assert prepared.identities == {"data": "synthetic.data", "terminology": "synthetic.knowledge",
                                   "knowledge": "cql"}
    assert prepared.request["environment"] == "synthetic-population" and "data_source" not in prepared.request


def test_explicit_targets_are_qualified_by_compiler_origin():
    """RUN-CLIENT-RESULT-SETS — a request does not silently expand or normalize its outputs."""
    assert prepare(Compiler(), targets=("Output",)).targets == ("Synthetic.Output",)
    for targets in ((), ("missing",), ("Output", "Synthetic.Output")):
        with pytest.raises(ValueError):
            prepare(Compiler(), targets=targets)


def test_period_precision_is_owned_by_the_compiler_not_inferred_by_the_client():
    """SVC-COMPILE-BATCH — transmit bounds unchanged for server-side declared-type normalization."""
    compiler = Compiler()
    prepare(compiler, period=("2026-01-01T12:34:56.000", "2026-12-31"))
    assert compiler.requests[0].measurement_period == ("2026-01-01T12:34:56.000", "2026-12-31")
    assert compiler.requests[-1].parameters == {}


def test_unsupported_compiler_contract_fails_during_preparation():
    """RUN-CLIENT-COMPLETION — no destination is provisioned for unsupported plans."""
    compiler = Compiler()
    compiler.plan.datasets[0] = replace(compiler.plan.datasets[0], durable=None)
    with pytest.raises(ValueError, match="durable identities"):
        prepare(compiler)


def test_a_value_set_used_completes_the_fingerprint_in_one_query():
    """CLIENT-RESOLVER-IDENTITY — the codes of the value sets a recipe uses are digested into its fingerprint, and a
    changed expansion changes it."""
    def compiled_with_value_set():
        compiler = Compiler()
        first = compiler.plan.datasets[0]
        compiler.plan.datasets[0] = replace(first, durable=replace(first.durable,
                                                                   value_sets=(ValueSetRef(id="urn:vs:a"),)))
        return compiler

    def digesting(digest):
        def runner(statement, params=None):
            assert "sha2(" in statement and "`synthetic`.`knowledge`.`valueset`" in statement
            return [{"layer": 0, "valueset_id": "urn:vs:a", "digest": digest}]
        return runner

    compiler = compiled_with_value_set()
    original = compiler.plan.datasets[0].durable.fingerprint
    one = prepare(compiler, run=digesting("1" * 64)).plan.datasets[0].durable.fingerprint
    two = prepare(compiled_with_value_set(), run=digesting("2" * 64)).plan.datasets[0].durable.fingerprint
    assert len({original, one, two}) == 3
    assert prepare(compiled_with_value_set(), run=digesting("1" * 64)).plan.datasets[0].durable.fingerprint == one
    untouched = prepare(compiled_with_value_set(), run=digesting("1" * 64)).plan.datasets[1].durable.fingerprint
    assert untouched == compiler.plan.datasets[1].durable.fingerprint


def test_a_library_by_name_records_the_knowledge_it_came_from():
    """RUN-CLIENT-DRAFT-RESULTS — an official store takes a library by name from the environment's knowledge, and
    nothing from a folder."""
    official = EnvironmentResolver(run).official_store(ENVIRONMENT)
    by_name = prepare(Compiler(), cql=None, library="Synthetic", version="1")
    assert by_name.identities["knowledge"] == "synthetic.knowledge"
    official.admit(by_name)
    # CQL text read with a folder's includes, as the Designer runs a folder library.
    folder = prepare(Compiler(), location="folder:/Workspace/m", files=_FolderFiles())
    assert folder.identities["knowledge"] == "cql"
    with pytest.raises(PermissionError, match="knowledge is cql"):
        official.admit(folder)


class _FolderFiles:
    """A folder holding the measure's own copy of its include."""

    FOLDER_INCLUDE = "library Helper version '1'\ndefine Base: 8"

    def is_dir(self, path):
        return path == "/Workspace/m"

    def glob(self, root, pattern):
        return ["/Workspace/m/cql/Helper-1.cql"] if pattern == "*/*.cql" else []

    def read_text(self, path):
        return self.FOLDER_INCLUDE

    def canonical(self, path):
        return path

    def directory_of(self, path):
        return path.rsplit("/", 1)[0]


def test_a_location_is_resolved_from_and_recorded_for_the_reopen():
    """RUN-ADDRESS-LOCATION — the folder's include wins over knowledge, and the request says where it came from."""
    compiler = Compiler()
    prepared = prepare(compiler, location="folder:/Workspace/m", files=_FolderFiles())
    assert compiler.requests[0].deps[0].cql == _FolderFiles.FOLDER_INCLUDE
    assert prepared.request["location"] == "folder:/Workspace/m"
    assert "files" not in prepared.request


def test_no_location_records_none():
    assert "location" not in prepare(Compiler()).request
