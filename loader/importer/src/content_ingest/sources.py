"""DATA-SOURCE-UC-TAG — the data sources this repository ships, and where their parts live.

Two sources, both fabricated: **Synthetic Population Small** (``DATA-SYNTH-SMALL``, the patients in
``examples/data`` the sample measures run over, synthesized from a recipe and landed by the deploy) and **Synthetic
Population Large** (``DATA-SYNTH-LARGE``, synthesized cohorts with per-patient expected results). A source's name is part of the address of every result
computed against it, so renaming one recomputes those results.

Both land through ``population.land``, which declares them with
``cql_databricks.data_bindings.declaration_statements`` after the data is in. Listing data sources as a caller
is ``cql_databricks.compiler.data_source.list_data_sources``.
"""
from __future__ import annotations

from .change_data_log import CHANGE_DATA_LOG_NAME

SYNTHETIC_SMALL_SOURCE_ID = "synthetic-population-small"
SYNTHETIC_SMALL_SCHEMA = "synthetic_population_small"
# Says "fabricated" in the description itself: the failure that matters is a sample count read as a measured one.
SYNTHETIC_SMALL_DESCRIPTION = "Fabricated small population, synthesized for the sample measures"

# "Large", not a count: the population grows with each measure's cohort, so a count in the name goes stale.
SYNTHETIC_LARGE_SOURCE_ID = "synthetic-population-large"
SYNTHETIC_LARGE_SCHEMA = "synthetic_population_large"
SYNTHETIC_LARGE_DESCRIPTION = "Large synthesized patient population, with expected results per patient"


def change_data_log_fqn(catalog: str, schema: str) -> str:
    """DATA-CHANGE-LOG — where a shipped source's change log lives, beside its mounts.

    ``population.land`` creates the view here, and the compiler's layout check finds it by the same name, so the
    address has one definition (`PROC-DRY`).
    """
    return f"{catalog}.{schema}.{CHANGE_DATA_LOG_NAME}"
