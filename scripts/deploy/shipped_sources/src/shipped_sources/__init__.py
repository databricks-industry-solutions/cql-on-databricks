"""shipped-sources — the deploy's own data: the example content and the data sources this repository ships
(DEPLOY-SHIPPED-SOURCES).

The example measures and patients in ``examples/`` (``examples.py``, run by the deploy,
``CONTENT-EXAMPLE-PRELOAD``), imported through the client's one import path
(``cql_databricks.data_bindings.measure_import``, ``CONTENT-IMPORT-MEASURES``). It also holds the shipped data
sources' parts (``population.py``, ``sources.py``), which ``loader/scripts/synthetic-population/load.py`` uses too.
Never shipped to the Volume.
"""
