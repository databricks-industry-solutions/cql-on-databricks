// APP-VALUE-READABLE — the value renderer, pinned against the shapes the wire actually carries.
//
// The fixtures are the three live relation shapes (`cql_databricks/result/relation.py`): a boolean define, a
// scalar define whose value column is a STRUCT, and a list-valued define whose `__val` is a resource.
// The STRUCT cases arrive as `to_json` STRINGS, which is what the pane was rendering verbatim.

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { readValue, summarizeValue } from "./defineValue.ts";
import { valueShapeOf, type MetricValueShape } from "./specDocument.ts";
import { SampleRow } from "./Workbench.tsx";

describe("readValue — scalars (APP-VALUE-READABLE)", () => {
  it("reads a boolean define's value as membership, not as a literal", () => {
    // `relation.met_expr` makes `true` the membership predicate for a boolean define, so these are the
    // words the rest of the pane already uses ("N met").
    expect(summarizeValue(true)).toBe("Met");
    expect(summarizeValue(false)).toBe("Not met");
  });

  it("distinguishes null from false", () => {
    // CQL's three-valued logic: both are "not in the population", but they are different facts.
    expect(summarizeValue(null)).toBe("—");
    expect(summarizeValue(undefined)).toBe("—");
    expect(summarizeValue(false)).toBe("Not met");
  });

  it("leaves a non-JSON string alone", () => {
    expect(summarizeValue("finished")).toBe("finished");
    expect(summarizeValue("Patient/abc")).toBe("Patient/abc");
  });

  it("groups a number and gives a scalar no fields", () => {
    const read = readValue(2713);
    expect(read.summary).toBe("2,713");
    expect(read.fields).toEqual([]);
  });
});

describe("readValue — dates (APP-VALUE-READABLE)", () => {
  it("writes a date in the one form that is unambiguous in every locale", () => {
    expect(summarizeValue("2024-03-12")).toBe("12 Mar 2024");
    expect(summarizeValue("2024-03")).toBe("Mar 2024");
  });

  it("keeps a stated time of day but not a widened midnight", () => {
    expect(summarizeValue("2024-03-12T14:30:00Z")).toBe("12 Mar 2024 14:30");
    expect(summarizeValue("2024-03-12T00:00:00+00:00")).toBe("12 Mar 2024");
  });

  it("does not read a code as a date", () => {
    // The regression the anchored pattern exists for: an unanchored one rendered a numeric code
    // shaped like `1000-1` as the year 1000.
    expect(summarizeValue("1000-1")).toBe("1000-1");
  });
});

describe("readValue — a scalar define's STRUCT (APP-VALUE-READABLE)", () => {
  it("reads a CQL Quantity arriving as to_json output", () => {
    const read = readValue('{"value":139,"unit":"mm[Hg]"}');
    expect(read.summary).toBe("139 mm[Hg]");
    // The line says the whole value, so there is nothing left to disclose.
    expect(read.fields).toEqual([]);
  });

  it("takes the unit from `code` when `unit` is absent, as UCUM-coded Quantities carry it", () => {
    expect(summarizeValue('{"value":80,"code":"mm[Hg]","system":"http://unitsofmeasure.org"}'))
      .toBe("80 mm[Hg]");
  });

  it("keeps the raw value untouched for the tooltip", () => {
    const raw = '{"value":139,"unit":"mm[Hg]"}';
    expect(readValue(raw).raw).toBe(raw);
  });
});

describe("readValue — coded values (APP-VALUE-READABLE)", () => {
  it("leads with the human display, not the code", () => {
    expect(summarizeValue({
      coding: [{ system: "http://loinc.org", code: "X0001", display: "Example Reading A" }],
    })).toBe("Example Reading A");
  });

  it("falls to the concept text when the codings carry no display", () => {
    expect(summarizeValue({ coding: [{ system: "http://loinc.org", code: "X0001" }], text: "Example Reading A" }))
      .toBe("Example Reading A");
  });

  it("spells the code with the system's clinical name when there is no display at all", () => {
    expect(summarizeValue({ system: "http://loinc.org", code: "X0001" })).toBe("LOINC X0001");
    expect(summarizeValue({ system: "http://snomed.info/sct", code: "38341003" }))
      .toBe("SNOMED CT 38341003");
  });

  it("names an unknown system from its URL's last segment rather than printing the URL", () => {
    expect(summarizeValue({ system: "http://terminology.hl7.org/CodeSystem/v3-ActCode", code: "AMB" }))
      .toBe("v3-ActCode AMB");
  });
});

describe("readValue — spans (APP-VALUE-READABLE)", () => {
  it("states both ends of a FHIR Period and a CQL Interval", () => {
    expect(summarizeValue({ start: "2024-03-12", end: "2024-03-18" }))
      .toBe("12 Mar 2024 – 18 Mar 2024");
    expect(summarizeValue({ low: "2024-01-01", high: "2024-12-31" }))
      .toBe("1 Jan 2024 – 31 Dec 2024");
  });

  it("says an open end in words — a bare date would read as a point in time", () => {
    expect(summarizeValue({ start: "2024-03-12" })).toBe("from 12 Mar 2024");
    expect(summarizeValue({ high: "2024-12-31" })).toBe("until 31 Dec 2024");
  });
});

describe("readValue — a resource (APP-VALUE-READABLE)", () => {
  // A list-valued define's `__val`, as `to_json` renders it: the typed FHIR STRUCT with its absent
  // elements still present as nulls, empty strings and empty collections.
  const encounter = JSON.stringify({
    resourceType: "Encounter",
    id: "enc-8821",
    meta: { versionId: null, lastUpdated: null },
    text: {},
    implicitRules: null,
    language: "",
    identifier: [],
    status: "finished",
    class: { system: "http://terminology.hl7.org/CodeSystem/v3-ActCode", code: "AMB", display: "ambulatory" },
    type: [{ coding: [{ system: "http://www.ama-assn.org/go/cpt", code: "X0001", display: "Example Visit" }] }],
    subject: { reference: "Patient/a3f2c1d4-55e6-4b77-8899-aabbccddeeff" },
    period: { start: "2024-03-12T09:15:00Z", end: "2024-03-12T09:48:00Z" },
    reasonCode: [],
    length: null,
  });

  it("summarizes the resource as what it is, when it happened and its state", () => {
    expect(readValue(encounter).summary)
      .toBe("Example Visit · 12 Mar 2024 09:15 – 12 Mar 2024 09:48 · finished");
  });

  it("drops every empty field — the whole reason the block was unreadable", () => {
    const labels = readValue(encounter).fields.map((f) => f.label);
    // `identifier: []`, `reasonCode: []`, `length: null`, `language: ""` all state nothing.
    expect(labels).not.toContain("Identifier");
    expect(labels).not.toContain("Reason");
    expect(labels).not.toContain("Length");
    expect(labels).not.toContain("Language");
  });

  it("drops the envelope, which is most of the bytes and none of the answer", () => {
    const labels = readValue(encounter).fields.map((f) => f.label);
    for (const noise of ["Meta", "Text", "Resource type"]) expect(labels).not.toContain(noise);
  });

  it("orders the fields the way a reviewer scans, not the way FHIR declares them", () => {
    expect(readValue(encounter).fields.map((f) => f.label))
      .toEqual(["Type", "Class", "Period", "Status", "Patient", "Id"]);
  });

  it("gives a coded field its code, which the summary line has no room for", () => {
    const byLabel = new Map(readValue(encounter).fields.map((f) => [f.label, f.value]));
    expect(byLabel.get("Type")).toBe("Example Visit (CPT X0001)");
    expect(byLabel.get("Class")).toBe("ambulatory (v3-ActCode AMB)");
  });

  it("shortens a reference's UUID — 36 characters say only that there is one", () => {
    const byLabel = new Map(readValue(encounter).fields.map((f) => [f.label, f.value]));
    expect(byLabel.get("Patient")).toBe("Patient/a3f2c1d4…");
  });

  it("marks ids and figures as machine data so the mono rule can be applied per field", () => {
    const byLabel = new Map(readValue(encounter).fields.map((f) => [f.label, f.code]));
    expect(byLabel.get("Id")).toBe(true);
    expect(byLabel.get("Status")).toBe(false);
    expect(byLabel.get("Type")).toBe(false);
  });

  it("keeps the whole value, empties and all, on the tooltip", () => {
    // Nothing is hidden by the pruning — it is demoted. The raw text still carries `identifier: []`.
    expect(readValue(encounter).raw).toContain("identifier");
  });

  it("leads with the resource type only when nothing more specific is present", () => {
    expect(summarizeValue('{"resourceType":"Encounter","id":"e1"}')).toBe("Encounter");
    expect(summarizeValue('{"resourceType":"MedicationRequest","id":"m1"}')).toBe("Medication request");
  });
});

describe("readValue — an Observation with components (APP-VALUE-READABLE)", () => {
  const bp = JSON.stringify({
    resourceType: "Observation",
    status: "final",
    code: { coding: [{ system: "http://loinc.org", code: "X0003", display: "Example Reading Panel" }] },
    effectiveDateTime: "2024-06-02T11:05:00Z",
    valueQuantity: null,
    component: [
      { code: { coding: [{ system: "http://loinc.org", code: "X0001", display: "Example Reading A" }] },
        valueQuantity: { value: 139, unit: "mm[Hg]" } },
      { code: { coding: [{ system: "http://loinc.org", code: "X0002", display: "Example Reading B" }] },
        valueQuantity: { value: 88, unit: "mm[Hg]" } },
    ],
  });

  it("reads the panel by its code, date and status", () => {
    expect(readValue(bp).summary).toBe("Example Reading Panel · 2 Jun 2024 11:05 · final");
  });

  it("reads each component as its own code and its own measurement", () => {
    const byLabel = new Map(readValue(bp).fields.map((f) => [f.label, f.value]));
    expect(byLabel.get("Components")).toBe("Example Reading A · 139 mm[Hg] +1 more");
  });

  it("omits the null valueQuantity a panel carries at the top level", () => {
    expect(readValue(bp).fields.map((f) => f.label)).not.toContain("Value");
  });
});

describe("readValue — lists (APP-VALUE-READABLE)", () => {
  it("summarizes the first element and counts the rest", () => {
    const read = readValue([
      { system: "http://loinc.org", code: "X0001", display: "Example Reading A" },
      { system: "http://loinc.org", code: "X0002", display: "Example Reading B" },
    ]);
    expect(read.summary).toBe("Example Reading A +1 more");
    expect(read.fields.map((f) => f.value)).toEqual([
      "Example Reading A (LOINC X0001)", "Example Reading B (LOINC X0002)",
    ]);
  });

  it("says a value is absent when a list pruned away to nothing", () => {
    expect(summarizeValue([null, "", []])).toBe("—");
  });
});

describe("the top-N row in the pane (APP-VALUE-READABLE)", () => {
  // The row is the thing that was showing JSON, so the reading is asserted where the reader meets it.
  const encounter = JSON.stringify({
    resourceType: "Encounter", id: "enc-1", status: "finished", identifier: [], reasonCode: [],
    type: [{ coding: [{ system: "http://www.ama-assn.org/go/cpt", code: "X0001", display: "Example Visit" }] }],
    period: { start: "2024-03-12" },
  });

  const row = (value: string, count = 88, share = 0.42, shape: MetricValueShape = "row") => (
    <SampleRow sample={{ value, count, share }} shape={shape} />
  );

  it("labels the row with the reading and never with the JSON", () => {
    render(row(encounter));
    expect(screen.getByText("Example Visit · from 12 Mar 2024 · finished")).toBeTruthy();
    expect(screen.queryByText(/resourceType/)).toBeNull();
  });

  it("keeps the untouched value reachable on the label's tooltip", () => {
    render(row(encounter));
    expect(screen.getByText("Example Visit · from 12 Mar 2024 · finished").getAttribute("title"))
      .toBe(encounter);
  });

  it("counts an AGGREGATED value, where the figure is the distribution", () => {
    render(row("true", 1288, 0.42, "value"));
    expect(screen.getByText("1,288")).toBeTruthy();
  });

  it("counts NO row at `row` shape — one record per line makes it a column of 1s", () => {
    // At `row` shape each line is one RECORD, so the count is 1 all the way down. This column would only
    // tell a reader that the define returns records — which the rows already show by being records.
    render(row(encounter, 1, 0.125, "row"));
    expect(screen.queryByText("1")).toBeNull();
  });

  it("draws NO share bar over rows — resources are near-unique, so a frequency over them is not one", () => {
    const { container } = render(row(encounter, 1, 0.125, "row"));
    expect(container.querySelector(".mw-wb-bar")).toBeNull();
  });

  it("draws the share bar over values that aggregate", () => {
    const { container } = render(row("true", 1204, 0.42, "value"));
    expect(container.querySelector(".mw-wb-bar > span")?.getAttribute("style")).toContain("42%");
  });

  it("keeps the fields folded away until the row is clicked", () => {
    render(row(encounter));
    expect(screen.queryByText("Example Visit (CPT X0001)")).toBeNull();
  });

  it("expands from a click ANYWHERE on the row, not from a labelled control", () => {
    render(row(encounter));
    // The whole row is the button, so the label a reader is looking at is a valid target for the click.
    fireEvent.click(screen.getByText("Example Visit · from 12 Mar 2024 · finished"));
    // Type, Period, Status, Id — the two empty collections and the resource type are not fields.
    expect(screen.getByText("Example Visit (CPT X0001)")).toBeTruthy();
    expect(screen.getByText("Id")).toBeTruthy();
    expect(screen.queryByText("Identifier")).toBeNull();
  });

  it("collapses again on a second click", () => {
    render(row(encounter));
    const control = screen.getByRole("button");
    fireEvent.click(control);
    fireEvent.click(control);
    expect(screen.queryByText("Example Visit (CPT X0001)")).toBeNull();
  });

  it("offers no expansion for a value that its own line fully states", () => {
    // A boolean has nothing under it, so the row is a disabled button — the icon slot still holds its
    // width so the counts stay in one column, but there is nothing to press.
    render(row("true", 67, 1, "value"));
    expect(screen.getByRole("button").hasAttribute("disabled")).toBe(true);
  });
});

describe("valueShapeOf — may a frequency be drawn over these values? (APP-VALUE-READABLE)", () => {
  it("aggregates a boolean, which is the case the bar exists for", () => {
    expect(valueShapeOf("Boolean")).toBe("value");
  });

  it("aggregates scalars and the shapes that are one value in several parts", () => {
    for (const t of ["Integer", "Decimal", "Long", "String", "Quantity", "FHIR.Quantity",
      "DateTime", "Date", "Code", "Concept", "FHIR.CodeableConcept", "FHIR.Period",
      "Interval<DateTime>", "FHIR.dateTime", "FHIR.string"]) {
      expect(valueShapeOf(t), t).toBe("value");
    }
  });

  it("treats a resource as a ROW, listed or not", () => {
    expect(valueShapeOf("List<FHIR.Encounter>")).toBe("row");
    expect(valueShapeOf("FHIR.Encounter")).toBe("row");
    expect(valueShapeOf("List<FHIR.Observation>")).toBe("row");
    // A tuple is an anonymous record — a row with no type name.
    expect(valueShapeOf("Tuple")).toBe("row");
  });

  it("unwraps a list, because a list of X and one X are the same KIND of thing", () => {
    expect(valueShapeOf("List<Boolean>")).toBe("value");
    expect(valueShapeOf("List<Integer>")).toBe("value");
  });

  it("defaults to ROW for a type it does not know, and for no type at all", () => {
    // Only one of the two errors states something false: read as a value, an unknown type draws a bar
    // asserting a frequency nobody measured; read as a row it just declines to draw one. With the
    // translator run without `EnableResultTypes` there is nothing to dispatch on at all.
    expect(valueShapeOf("FHIR.SomeResourceInventedNextYear")).toBe("row");
    expect(valueShapeOf(null)).toBe("row");
  });
});

describe("readValue — values it does not recognize (APP-VALUE-READABLE)", () => {
  it("names an unrecognized object's own single field rather than guessing", () => {
    expect(summarizeValue({ episodeOfCare: "acute" })).toBe("Episode of care acute");
  });

  it("says how many fields an unreadable object has instead of inventing a reading", () => {
    expect(summarizeValue({ alpha: 1, beta: 2, gamma: 3 })).toBe("3 fields");
  });

  it("treats a string that is not JSON as the string it is, brace or no brace", () => {
    expect(summarizeValue("{not json")).toBe("{not json");
  });
});
