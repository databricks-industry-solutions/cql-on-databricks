"""APP-DATA-PROFILE / APP-PROFILE-STRATA — the profile's wire contract and the sample seam.

These tests pin the CONTRACT, not the sample figures. The numbers are invented and will be replaced by
a run-backed builder; what must survive that swap is the shape, the provenance stamp, the grain/unit
pairing on every stratum, and the rule that absent means absent rather than zero.
"""

from __future__ import annotations

from cql_databricks.result.insights import PROVENANCE_SAMPLE, sample_profile

_LIB = "ExampleMeasure"


def test_every_payload_is_stamped_sample() -> None:
    # The mark is what keeps APP-NO-UI-MOCK enforceable while the derivation is built: a bar chart of
    # invented numbers is indistinguishable from a real one unless the payload says so.
    p = sample_profile(_LIB, "Example Results").to_dict()
    assert p["provenance"] == PROVENANCE_SAMPLE
    assert p["caveat"], "a sample payload must carry the caveat the client renders"


def test_profile_is_deterministic_in_the_qualified_name() -> None:
    # A generator that reshuffled per request would make screenshots irreproducible and the pane look
    # alive when nothing is running.
    first = sample_profile(_LIB, "Example Results").to_dict()
    second = sample_profile(_LIB, "Example Results").to_dict()
    assert first["values"] == second["values"]
    assert first["strata"] == second["strata"]
    # …and library-qualified, because two libraries may declare one name (SPEC-ANCHOR).
    other = sample_profile("OtherLibrary", "Example Results").to_dict()
    assert other["values"] != first["values"]


def test_no_result_type_is_served_the_client_derives_it() -> None:
    # SPEC-METRIC / APP-SPEC-MODEL — result types come from the translator's ELM and are parsed
    # client-side. A second server-side copy could disagree with the first.
    p = sample_profile(_LIB, "Example Results").to_dict()
    assert "resultType" not in p
    assert "returns" not in p


def test_every_stratum_names_its_grain_and_unit() -> None:
    # The two grains are the point (SPEC-METRIC): one patient with multiple list items contributes items to an
    # element cut and one to a subject cut, so a bar with no unit cannot be read.
    p = sample_profile(_LIB, "Example Results").to_dict()
    assert p["strata"], "an event-valued define should carry stratifications"
    for s in p["strata"]:
        assert s["grain"] in {"subject", "element"}
        assert s["unit"], f"stratum {s['id']} has no unit"
        assert s["bins"], f"stratum {s['id']} has no bins"


def test_shares_are_computed_from_dealt_counts() -> None:
    # Bar widths have to agree with the numbers printed beside them, so shares normalise over the counts
    # actually dealt rather than over the generator's weights.
    p = sample_profile(_LIB, "Example Results").to_dict()
    for s in p["strata"]:
        total = sum(b["share"] for b in s["bins"])
        assert abs(total - 1.0) < 0.01, f"{s['id']} shares sum to {total}"


def test_every_cut_of_one_grain_sums_to_the_same_total() -> None:
    # Three cuts of the same items reported different sums while the header said a different total,
    # because each bucket truncated independently. It read as a data bug, and in the real builder it
    # would BE one: a stratification of N that does not sum to N dropped or double-counted a subject.
    p = sample_profile(_LIB, "Example Results").to_dict()
    values = p["values"]
    for s in p["strata"]:
        expected = values["subjects"] if s["grain"] == "subject" else values["elements"]
        assert sum(b["count"] for b in s["bins"]) == expected, \
            f"{s['id']} sums to {sum(b['count'] for b in s['bins'])}, expected {expected}"


def test_event_valued_define_carries_both_demographic_and_date_cuts() -> None:
    p = sample_profile(_LIB, "Example Results").to_dict()
    ids = {s["id"] for s in p["strata"]}
    # Demographic, at subject grain.
    assert {"age", "sex", "race", "ethnicity", "payer"} <= ids
    # Date, at element grain — the cuts an event-valued define wants.
    assert {"month", "weekday", "quarter"} <= ids
    element_units = {s["unit"] for s in p["strata"] if s["grain"] == "element"}
    assert element_units == {"observations"}


def test_boolean_define_has_no_element_grain_and_no_code_table() -> None:
    # A Boolean says something about the subject and nothing else, so an element count would be an
    # invented dimension and a code table would be a value set it does not read.
    p = sample_profile(_LIB, "Denominator").to_dict()
    assert "elements" not in p["values"]
    assert "cardinality" not in p["values"]
    assert "valueSets" not in p
    assert "temporal" not in p
    assert all(s["grain"] == "subject" for s in p["strata"])


def test_declared_code_count_is_absent_not_zero() -> None:
    # "0 codes declared" and "we have not looked" are opposite claims. The terminology read path does not
    # exist, so the field is omitted and the client adapts its headline (APP-LIVING-CHARTS).
    p = sample_profile(_LIB, "Example Results").to_dict()
    for vs in p["valueSets"]:
        assert "declared" not in vs
        assert vs["fired"] > 0
        assert vs["top"], "a fired count with no codes behind it is unusable"


def test_temporal_holds_the_look_back_apart_from_the_period() -> None:
    # A define whose events all land outside the period is an anchoring bug, and merging the two bands
    # would draw the same bars while hiding exactly that.
    p = sample_profile(_LIB, "Example Results").to_dict()
    bands = [b["inPeriod"] for b in p["temporal"]]
    assert False in bands and True in bands
    # The look-back leads, so the chart reads left-to-right in time.
    assert bands.index(True) > bands.index(False)


def test_archetype_reads_the_resource_noun_before_the_predicate() -> None:
    # `Results During Period` and `Encounters During Period` are both list-valued — a leading
    # "qualif" test profiled both as Booleans, which is why `qualif` is not a cohort keyword.
    results = sample_profile(_LIB, "Results During Period").to_dict()
    encounters = sample_profile(_LIB, "Encounters During Period").to_dict()
    assert results["values"]["elements"] > 0
    assert encounters["values"]["elements"] > 0
    assert {s["unit"] for s in encounters["strata"] if s["grain"] == "element"} == {"encounters"}
    assert {s["unit"] for s in results["strata"] if s["grain"] == "element"} == {"observations"}
