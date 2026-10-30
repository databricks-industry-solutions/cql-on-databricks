"""The synthetic population small in ``examples/data`` is what its recipe builds (DATA-SYNTH-SMALL,
SYNTH-PDS-DETERMINISM).

The population is committed so a deploy lands it with nothing to synthesize. This test rebuilds it from the recipe
and the pinned fixtures and compares the bytes, so a change to an example measure, its config or the synthesizer that
moves the data fails here until the population is rebuilt. It skips when a fixture is not built.

The synthetic population large is not committed (DATA-SYNTH-LARGE): its recipe is only checked to name pinned
measures, so scripts/generate-large-data.sh can build every cohort it lists.
"""

from __future__ import annotations

import json

import pytest

from cql_databricks_synth import SynthesizerError
from cql_databricks_synth.fixtures import dir_or_skip, load_pins, repo_root
from cql_databricks_synth.recipe import Recipe, build_population

SMALL = repo_root() / "examples" / "data"
RECIPES = repo_root() / "loader" / "synthesizer" / "recipes"
RECIPE = RECIPES / "synthetic-small.json"


def test_the_committed_small_population_is_what_its_recipe_builds(tmp_path):
    recipe = Recipe.from_file(RECIPE)
    for cohort in recipe.cohorts:
        dir_or_skip(cohort.measure)
    build_population(recipe, tmp_path)
    built = {path.name: path.read_bytes() for path in sorted(tmp_path.glob("*.ndjson"))}
    committed = {path.name: path.read_bytes() for path in sorted(SMALL.glob("*.ndjson"))}
    assert sorted(built) == sorted(committed)
    stale = [name for name in built if built[name] != committed[name]]
    assert not stale, (f"{stale} differ from what the recipe builds — regenerate: scripts/generate-example-data.sh")


@pytest.mark.parametrize("name", ["synthetic-small.json", "synthetic-large.json"])
def test_every_recipe_cohort_is_a_pinned_measure(name):
    recipe = Recipe.from_file(RECIPES / name)
    unpinned = [cohort.measure for cohort in recipe.cohorts if cohort.measure not in load_pins()]
    assert not unpinned, f"{name} names measures with no pin in pins.json: {unpinned}"


def test_a_recipe_with_a_repeated_seed_fails(tmp_path):
    path = tmp_path / "recipe.json"
    path.write_text(json.dumps({"patients": 10, "measurementPeriod": "2026-01-01/2026-12-31",
                                "cohorts": [{"measure": "a", "seed": 1}, {"measure": "b", "seed": 1}]}))
    with pytest.raises(SynthesizerError, match="repeat"):
        Recipe.from_file(path)
