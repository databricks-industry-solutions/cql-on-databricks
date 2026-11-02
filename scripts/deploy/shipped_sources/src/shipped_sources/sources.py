"""CLIENT-ENVIRONMENT — the environments this repository ships, by name.

Two, both fabricated: **Synthetic Population Small** (``DATA-SYNTH-SMALL``), the default environment, which the
deploy builds in ``uc_catalog`` with the patients in ``examples/data`` and the example measures; and **Synthetic
Population Large** (``DATA-SYNTH-LARGE``), an environment of its own, in its own catalog, landed by hand. An
environment's name is the value of its catalog's ``cql_databricks_environment`` tag, and its description the catalog
comment (`CLIENT-ENVIRONMENT`).
"""
from __future__ import annotations

SYNTHETIC_SMALL_ENVIRONMENT = "synthetic-population-small"
# Says "fabricated" in the description itself: the failure that matters is a fabricated count read as a measured one.
SYNTHETIC_SMALL_DESCRIPTION = "Fabricated small population and the example measures"

# "Large", not a count: the population grows with each measure's cohort, so a count in the name goes stale.
SYNTHETIC_LARGE_ENVIRONMENT = "synthetic-population-large"
SYNTHETIC_LARGE_DESCRIPTION = "Large synthesized patient population, with expected results per patient"
