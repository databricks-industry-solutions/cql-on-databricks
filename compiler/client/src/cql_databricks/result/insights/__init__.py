"""APP-DATA-PROFILE / APP-SPEC-MODEL — the figures the Designer renders.

Two payloads, one boundary. `DefineProfile` is what the right pane's Data tab asks about ONE define;
`MeasureMetrics` is every figure the document, the nav and the reported result share, fetched once so
they cannot disagree.

`model.py` is the wire contract and survives. **`metrics.py` is the warehouse-backed metric builder**
replacing `sample.py`'s metric half. `sample.py` retains the per-define PROFILE, which is scaffolding until the
distribution reads land.
"""

from __future__ import annotations

from .model import (
    PROVENANCE_MIXED,
    PROVENANCE_RUN,
    PROVENANCE_SAMPLE,
    Bin,
    CodeHit,
    DefineMetric,
    DefineProfile,
    MeasureMetrics,
    Stratum,
    TemporalBin,
    ValueProfile,
    ValueSample,
    ValueSetHits,
)
from .evaluation import MetricsUnavailable
from .metrics import measure_metrics
from .profile import define_profile
from .score import MeasureScore, measure_score, parse_scoring, selection_score
from .sample import sample_profile

__all__ = [
    "PROVENANCE_MIXED",
    "PROVENANCE_RUN",
    "PROVENANCE_SAMPLE",
    "Bin",
    "CodeHit",
    "DefineMetric",
    "DefineProfile",
    "MeasureMetrics",
    "MeasureScore",
    "MetricsUnavailable",
    "Stratum",
    "TemporalBin",
    "ValueProfile",
    "ValueSample",
    "ValueSetHits",
    "define_profile",
    "measure_metrics",
    "measure_score",
    "parse_scoring",
    "sample_profile",
    "selection_score",
]
