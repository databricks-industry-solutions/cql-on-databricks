"""APP-NO-UI-MOCK — the synthetic Test Deck funnel, computed on the BFF (not the SPA).

The Test Deck is the OFFLINE compute mode (APP-EXEC-MODE): a deterministic, plausible eCQM funnel over
the population defines a measure declares, so the funnel / Patient Lens / charts are demoable with NO
live-data connection. The SPA fabricates no data (APP-NO-UI-MOCK), so the generator lives here
and the deck is API-served (`POST /api/sample-run`). "Offline" = no live-data/Spark connection — the BFF
serves it with no compiler and no session; it is deterministic (a fixed seed) and clearly SYNTHETIC (subject
ids are `deck-NNN`), never presented as real data.
"""
from __future__ import annotations

import math
import re

# The canonical eCQM population order (mirrors src/measures/model.ts POP_ORDER). A define present in the
# source becomes a funnel stage; the fixed fallback is used when a measure declares none by these names.
POP_ORDER = [
    "Initial Population",
    "Denominator",
    "Denominator Exclusions",
    "Denominator Exceptions",
    "Numerator",
    "Numerator Exclusions",
]
_FALLBACK_POPS = ["Initial Population", "Denominator", "Numerator"]
_N = 60  # synthetic member count


def _rand(i: int, k: int) -> float:
    """A deterministic pseudo-random value in [0, 1) from (member, slot) — stable across calls/processes
    (the same fixed-seed hash the former client used), so the Test Deck funnel never shifts under a user."""
    x = math.sin((i + 1) * 97.13 + k * 41.7) * 43758.5453
    return x - math.floor(x)


def sample_run(cql: str) -> dict:
    """A deterministic synthetic run for the Test Deck (APP-EXEC-MODE): a plausible eCQM funnel (60
    members) over the population defines the source declares, computed with no backend. Returns the shaped
    RunResult ({ok, results, counts}) the SPA renders — the SAME shape as a real /api/run."""
    declared = [n for n in POP_ORDER if re.search(r'define\s+"' + re.escape(n) + r'"', cql or "")]
    pops = declared or _FALLBACK_POPS
    results: dict[str, dict[str, bool]] = {}
    for i in range(_N):
        ipp = _rand(i, 1) < 0.9
        den = ipp and _rand(i, 2) < 0.95
        den_ex = den and _rand(i, 3) < 0.06
        num = den and not den_ex and _rand(i, 4) < 0.24
        num_ex = num and _rand(i, 5) < 0.03
        by_pop = {"Initial Population": ipp, "Denominator": den, "Denominator Exclusions": den_ex,
                  "Numerator": num, "Numerator Exclusions": num_ex}
        results[f"deck-{i + 1:03d}"] = {p: bool(by_pop.get(p, False)) for p in pops}
    counts = {p: sum(1 for r in results.values() if r[p]) for p in pops}
    return {"ok": True, "results": results, "counts": counts}
