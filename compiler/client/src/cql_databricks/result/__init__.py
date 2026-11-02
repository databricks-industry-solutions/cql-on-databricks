"""Durable bulk calculations and their results, in the client (`RESULT-INTO-CLIENT`).

``materialize`` executes the SQL the compiler returns through the caller's own SQL session and writes Delta
tables the caller owns (`RUN-CLIENT-DURABLE`). The readers and the result views (``insights``) read those tables as
the caller. A result is found by its table name, which carries its fingerprint, and its UC tags say it is complete
(`RUN-CLIENT-RESULT-IDENTITY`). Another client discovers and reuses it the same way.
"""
from ._catalog_runtime import PlanObserver, StepObserver, StepRecord
from .calculation import PreparedCalculation, prepare_calculation
from .inline import InlineRun, run_inline
from .durable import (MaterializedResults, ResultsUnavailable, archive_session, materialize, open_result_set,
                      resolve_results)
from .reference import ResultReference
# CLIENT-RESULT-RESOLVER: kept results, found in your own schemas then the official ones, and written, in Delta.
from .store import DeltaResultResolver, DeltaResultStore

__all__ = ["ResultReference", "DeltaResultResolver", "DeltaResultStore",
           "MaterializedResults", "ResultsUnavailable", "archive_session", "materialize", "open_result_set",
           "resolve_results",
           "PreparedCalculation", "prepare_calculation", "PlanObserver", "StepObserver", "StepRecord",
           "InlineRun", "run_inline"]
