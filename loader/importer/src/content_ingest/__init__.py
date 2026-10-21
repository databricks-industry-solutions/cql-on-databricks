"""content-ingest — loads CQL measure content into Unity Catalog (CONTENT-INGEST).

Two inputs, one set of table writers (``load.py``) and tags: the sample measures and patients in ``examples/``
(``samples.py``, run by the deploy, ``CONTENT-SAMPLE-PRELOAD``), and one eCQI measure package a user names and
accepts the terms of (``ecqi.py``, ``CONTENT-ECQI-TERMS-GATE``). Zero dependency on the Apps or the engine compiler.
"""
