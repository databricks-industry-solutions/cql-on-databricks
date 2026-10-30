"""APP-NO-UI-MOCK — the BFF-served Test Deck funnel (ported from the former client-side sampleRun)."""
from cql_databricks_designer import sample_deck

_SRC = ('define "Initial Population": true\n'
        'define "Denominator": true\n'
        'define "Denominator Exclusions": false\n'
        'define "Numerator": false')


def test_ok_and_deterministic():
    run = sample_deck.sample_run(_SRC)
    assert run["ok"] is True
    assert sample_deck.sample_run(_SRC) == run  # stable across calls (fixed seed)


def test_counts_only_for_declared_populations():
    run = sample_deck.sample_run(_SRC)
    assert sorted(run["counts"]) == [
        "Denominator", "Denominator Exclusions", "Initial Population", "Numerator"]


def test_plausible_descending_funnel():
    c = sample_deck.sample_run(_SRC)["counts"]
    assert c["Initial Population"] > c["Denominator"] > c["Numerator"]


def test_counts_match_per_member_results():
    run = sample_deck.sample_run(_SRC)
    members = list(run["results"].values())
    assert len(members) == 60
    assert run["counts"]["Numerator"] == sum(1 for m in members if m["Numerator"])


def test_fallback_populations_when_none_declared():
    # A source declaring none of the canonical population names → the fixed fallback funnel.
    run = sample_deck.sample_run('define "Something Else": true')
    assert sorted(run["counts"]) == ["Denominator", "Initial Population", "Numerator"]
