"""RUN-CLIENT-RESULT-IDENTITY — adapt synthetic compiler identities and bind declared inputs."""
from dataclasses import replace

import pytest

from cql_databricks.compiler.providers import DatasetRecipe, DurableIdentity, PipelinePlan, ReadCoordinates
from cql_databricks.result._catalog_runtime import CurrentArtifact
from cql_databricks.result._durable_plan import PreparedPlan


def identity(fingerprint: str = "a" * 64, reusable: bool = True) -> DurableIdentity:
    return DurableIdentity(format="cql-result-identity-v1", fingerprint=fingerprint, reusable=reusable)


def plan():
    return PipelinePlan(compiler_version="synthetic-compiler", datasets=[
        DatasetRecipe(name="base", target="Base", reads=[], statements=[], query="SELECT 7 AS __val",
                      library="Synthetic", library_version="1", define="Base", durable=identity("a" * 64)),
        DatasetRecipe(name="output", target="Output", reads=["base"], statements=[],
                      query="WITH local AS (SELECT __val FROM base) SELECT __val + 3 FROM local",
                      library="Synthetic", library_version="1", define="Output", durable=identity("b" * 64)),
    ])


def artifact():
    return CurrentArtifact(table=("synthetic_catalog", "results", "attempt"))


def test_compiler_identities_become_definitions_without_rehashing_or_name_inference():
    """RUN-CLIENT-RESULT-IDENTITY — the adapter uses the compiler's fingerprint and dependency fields as given."""
    prepared = PreparedPlan(plan())
    assert prepared.definitions[1].reads == ("base",)
    assert [d.fingerprint for d in prepared.definitions] == ["a" * 64, "b" * 64]


def test_compiler_version_is_not_part_of_the_identity():
    """RUN-CLIENT-RESULT-IDENTITY — a new compiler build with the same SQL keeps every definition."""
    assert PreparedPlan(replace(plan(), compiler_version="another")).definitions == PreparedPlan(plan()).definitions
    assert PreparedPlan(replace(plan(), compiler_version="")).definitions == PreparedPlan(plan()).definitions


def test_bindings_are_query_local_and_leave_compiler_sql_unchanged():
    """RUN-CLIENT-COMPLETION — no shared temp views or string replacements in the compiler query."""
    compiled = plan()
    prepared = PreparedPlan(compiled)
    query = prepared.query_for("output", {"base": artifact()})
    assert query.startswith("WITH `base` AS (SELECT * FROM `synthetic_catalog`.`results`.`attempt`)")
    assert compiled.datasets[1].query in query
    assert prepared.query_for("base", {}) == compiled.datasets[0].query


def test_table_components_are_quoted_individually():
    """RUN-CLIENT-COMPLETION — punctuation in caller-owned identifiers is not executable SQL."""
    unusual = replace(artifact(), table=("synthetic.catalog", "schema`name", "attempt-name"))
    assert "`synthetic.catalog`.`schema``name`.`attempt-name`)" in PreparedPlan(plan()).query_for(
        "output", {"base": unusual})


@pytest.mark.parametrize("claims", [{}, {"base": artifact(), "extra": artifact()}])
def test_binding_requires_exact_declared_inputs(claims):
    """RUN-CLIENT-COMPLETION — omissions cannot fall through to a same-named catalog table."""
    with pytest.raises(ValueError, match="declared reads"):
        PreparedPlan(plan()).query_for("output", claims)


@pytest.mark.parametrize("claim", [None, replace(identity(), format="cql-recipe-v1"), replace(identity(), format="future")])
def test_old_or_unknown_compiler_contract_is_rejected(claim):
    """RUN-CLIENT-RESULT-IDENTITY — an older compiler's hashes cannot claim durable eligibility."""
    compiled = plan()
    compiled.datasets[0] = replace(compiled.datasets[0], durable=claim)
    with pytest.raises(ValueError, match="supported durable identities"):
        PreparedPlan(compiled)


def test_unknown_dependencies_are_rejected():
    """RUN-CLIENT-RESULT-IDENTITY — compiler claims cannot refer to absent recipes."""
    compiled = plan()
    compiled.datasets[1] = replace(compiled.datasets[1], reads=["absent"])
    with pytest.raises(ValueError, match="unknown compiler dependencies"):
        PreparedPlan(compiled)


def test_retrieve_discovery_metadata_excludes_unrelated_consumer_counts():
    """RUN-CLIENT-RESULT-IDENTITY — adding readers does not change an existing retrieve's definition."""
    compiled = plan()
    descriptor = ReadCoordinates(resource_type="Patient", consumed_by=["Output"], call_sites=1)
    compiled.datasets[0] = replace(compiled.datasets[0], retrieve_read=descriptor)
    initial = PreparedPlan(compiled).definitions[0]
    compiled.datasets[0] = replace(compiled.datasets[0], retrieve_read=replace(
        descriptor, consumed_by=["Output", "Unrelated"], call_sites=2))
    assert PreparedPlan(compiled).definitions[0] == initial
