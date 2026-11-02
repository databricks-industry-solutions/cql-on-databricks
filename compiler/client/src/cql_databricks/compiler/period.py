"""LANG-PARAM-INSTANT — the one canonical form of a measurement period's bounds, for the compile parameter
(``library.measurement_period_literal``).
"""
from __future__ import annotations


#: The point types a measurement period may be declared over (`LANG-PARAM-INSTANT`): ``Interval<DateTime>``,
#: what the eCQMs declare, and ``Interval<Date>``.
PERIOD_POINT_TYPES = ("DateTime", "Date")


def measurement_period_bounds(start: str, end: str, point_type: str = "DateTime") -> tuple[str, str]:
    """The period's two bounds in the canonical form of its DECLARED point type (`LANG-PARAM-INSTANT`): for
    ``DateTime`` a bare date widens to the instant it denotes (the rule below); for ``Date`` a datetime truncates to
    its date.

    >>> measurement_period_bounds("2026-01-01", "2026-12-31")
    ('2026-01-01T00:00:00.000', '2026-12-31T23:59:59.999')
    >>> measurement_period_bounds("2026-01-01T00:00:00.000", "2026-12-31", "Date")
    ('2026-01-01', '2026-12-31')
    """
    if point_type == "DateTime":
        def bound(v: str, end_of_day: bool) -> str:
            return v if "T" in v else f"{v}T{'23:59:59.999' if end_of_day else '00:00:00.000'}"
    elif point_type == "Date":
        def bound(v: str, end_of_day: bool) -> str:
            return v.split("T", 1)[0]
    else:
        raise ValueError(f"a measurement period over {point_type!r} has no canonical form; "
                         f"expected one of {', '.join(PERIOD_POINT_TYPES)}")
    return bound(start, False), bound(end, True)
