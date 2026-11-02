"""CLIENT-ENVIRONMENT, DATA-SYNTH-SMALL, DATA-SYNTH-LARGE — the environments this repository ships, by name."""
from __future__ import annotations

from shipped_sources.sources import (SYNTHETIC_LARGE_ENVIRONMENT, SYNTHETIC_SMALL_DESCRIPTION,
                                    SYNTHETIC_SMALL_ENVIRONMENT)


def test_the_shipped_environments_have_distinct_names():
    """Two catalogs tagged with one name fail every lookup (CLIENT-ENVIRONMENT-RESOLVER)."""
    assert SYNTHETIC_SMALL_ENVIRONMENT != SYNTHETIC_LARGE_ENVIRONMENT


def test_the_example_environment_says_it_is_fabricated():
    """A fabricated count must never read as a measured one."""
    assert "abricated" in SYNTHETIC_SMALL_DESCRIPTION
