"""RUN-CLIENT-MIGRATION — shared preparation without legacy result or change-log reads."""
from __future__ import annotations

from dataclasses import replace

import pytest

from cql_databricks.compiler.providers import PipelinePlan
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

    def data_source_schemas(self):
        return [TaggedSchema("synthetic", "population", "synthetic", "source")]


class Compiler:
    def __init__(self):
        self.requests = []
        self.translations = []
        compiled = plan()
        self.plan = PipelinePlan([replace(compiled.datasets[0], library="Helper"), compiled.datasets[1]],
                                 compiled.compiler_version)

    def resource_types(self):
        raise AssertionError("SVC-COMPILE-BATCH must not fetch resource types separately")

    def translate_result(self, source, includes):
        raise AssertionError("SVC-COMPILE-BATCH must not translate libraries separately")

    def compile_pipeline(self, request):
        self.requests.append(request)
        return self.plan


def run(statement, params=None):
    assert "information_schema.routines" in statement
    return [{"routine_name": "retrieve_patient"}]


def prepare(compiler, **changes):
    return prepare_calculation(**(dict(content=Content(), run=run, compiler=compiler, cql=ROOT,
        data_source="population", runtime_catalog="synthetic_runtime", period=("2026-01-01", "2026-12-31")) | changes))


def test_preparation_binds_actual_addresses_typed_period_and_include_closure_once():
    """RUN-CLIENT-IDENTITY — compiler contracts supply targets and identity; no SQL string patches."""
    compiler = Compiler()
    prepared = prepare(compiler)
    request = compiler.requests[0]
    assert request.durable is True
    assert request.cql == ROOT and request.elm == ""
    assert request.parameters == {}
    assert request.measurement_period == ("2026-01-01", "2026-12-31")
    assert request.data_source == {"catalog": "synthetic", "schema": "source", "routines": ["retrieve_patient"]}
    assert request.terminology_table == "synthetic_runtime.runtime.valuesets"
    assert request.udf_schema == "synthetic_runtime.runtime"
    assert len(request.deps) == 1 and request.deps[0].name == "Helper"
    assert request.deps[0].cql == INCLUDE and not request.deps[0].elm
    assert compiler.translations == []
    assert prepared.targets == ("Helper.Base", "Synthetic.Output")
    assert prepared.compile_pipeline(durable=True) is compiler.plan
    assert len(compiler.requests) == 1


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


def test_permission_denial_is_not_a_source_or_result_miss():
    """SEC-OBO — caller SQL authorization remains a loud failure."""
    def denied(*args):
        raise PermissionError("synthetic source denied")
    with pytest.raises(PermissionError, match="synthetic source denied"):
        prepare(Compiler(), run=denied)


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
