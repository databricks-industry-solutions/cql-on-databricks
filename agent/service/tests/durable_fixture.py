"""RUN-CLIENT-RESULT-SETS — synthetic selected-output transport fixture; no storage claim."""
from datetime import datetime, timezone
from types import SimpleNamespace

RESULT_SET = {"destination": ["synthetic", "results"],
              "session_id": "synthetic-session", "selection_id": "synthetic-selection",
              "request_hash": "synthetic-request", "computed_since": None,
              "targets": ["Synthetic.Output"]}


def reference():
    return SimpleNamespace(
        relation=lambda: "(SELECT * FROM `synthetic`.`results`.`output`)",
        columns=[("__subject_id", "string"), ("__val", "boolean")],
        origin={"library": "Synthetic", "version": "1", "define": "Output"},
        completed=SimpleNamespace(completed_at=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        results=SimpleNamespace(request={"environment": "synthetic"}, selection=SimpleNamespace(
            request_hash=RESULT_SET["request_hash"], computed_since=None,
            targets={"Synthetic.Output": "output"},
            session_id=RESULT_SET["session_id"], selection_id=RESULT_SET["selection_id"],
            destination=tuple(RESULT_SET["destination"]))))
