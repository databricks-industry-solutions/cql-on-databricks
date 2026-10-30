"""RUN-CLIENT-COMPLETION — identifier quoting shared by the result modules.

Completion evidence lives in UC tags on each result table (`RUN-CLIENT-CATALOG-CHECKPOINTS`).
"""
from __future__ import annotations

from ._durable_plan import _quoted


def table_sql(parts: tuple[str, ...]) -> str:
    """RUN-CLIENT-COMPLETION — quote each catalog identifier without interpreting its contents."""
    return ".".join(_quoted(part) for part in parts)
