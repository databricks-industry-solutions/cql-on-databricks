"""DATA-SOURCE-UC-TAG — the data sources this repository ships, and where their parts live.

Two sources, both fabricated: **Sample Patients** (``DATA-SAMPLE-SOURCE``, the patients in ``examples/data``
the sample measures run over, landed by the deploy) and **Synthetic Population Large** (``DATA-SYNTH-LARGE``,
synthesized cohorts with per-patient expected results). A source's name is part of the address of every result
computed against it, so renaming one recomputes those results.

Both land through ``population.land``, which declares them with
``cql_databricks.data_bindings.declaration_statements`` after the data is in. Listing data sources as a caller
is ``cql_databricks.compiler.data_source.list_data_sources``.
"""
from __future__ import annotations

from .change_data_log import CHANGE_DATA_LOG_NAME

SAMPLE_PATIENTS_SOURCE_ID = "sample-patients"
SAMPLE_PATIENTS_SCHEMA = "sample_patients"
# Says "fabricated" in the description itself: the failure that matters is a sample count read as a measured one.
SAMPLE_PATIENTS_DESCRIPTION = "Fabricated sample patients for the example measures"

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
