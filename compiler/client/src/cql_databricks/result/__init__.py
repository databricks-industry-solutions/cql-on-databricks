"""Durable bulk calculations and their results, in the client (`RESULT-INTO-CLIENT`).

``materialize`` executes the SQL the compiler returns through the caller's own SQL session and writes Delta
tables the caller owns (`RUN-CLIENT-DURABLE`). The readers and the result views (``insights``) read those tables as
the caller. A result is found by its table name, which carries its fingerprint, and its UC tags say it is complete
(`RUN-CLIENT-RESULT-IDENTITY`). Another client discovers and reuses it the same way.
"""
from ._catalog_runtime import PlanObserver, StepObserver, StepRecord
from .calculation import PreparedCalculation, prepare_calculation
from .inline import InlineRun, run_inline
from .durable import (MaterializedResults, caller, destination_of, ResultsUnavailable, archive_session, materialize, open_result_set,
                      resolve_results, search_results)
from .reference import ResultReference
# CLIENT-RESULT-STORE: kept results, in Delta.
from .store import DeltaResultResolver

__all__ = ["ResultReference", "DeltaResultResolver",
           "MaterializedResults", "ResultsUnavailable", "archive_session", "materialize", "open_result_set",
           "resolve_results", "search_results",
           "PreparedCalculation", "prepare_calculation", "PlanObserver", "StepObserver", "StepRecord",
           "InlineRun", "run_inline"]
