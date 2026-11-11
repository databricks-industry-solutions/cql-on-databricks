"""Each synthesized resource names its patient in the element FHIR R4 gives its type (SYNTH-MODULE).

Generated data lands on the same data contract as a customer's source, so the patient link is a field of the
type's ``resource`` struct in ``resource-schemas.json``: ``patient`` on AllergyIntolerance, Device and
Immunization, ``beneficiary`` on Coverage, ``subject`` on the rest.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from cql_databricks_synth.fill import materialize
from cql_databricks_synth.fixtures import repo_root
from cql_databricks_synth.solve import ResourceSketch, SolveState

_SCHEMAS = json.loads((repo_root() / "scripts" / "deploy" / "shipped_sources" / "src" / "shipped_sources"
                       / "resource-schemas.json").read_text())

#: Every type the compiler models with a patient context, except Patient: it is the patient, so it links to none.
_LINKED_TYPES = sorted(t for t, schema in _SCHEMAS.items() if schema["patientContext"] and t != "Patient")


def _ddl_fields(ddl: str) -> set[str]:
    """The top-level field names of a Spark DDL string: the first word of each comma-separated part outside
    ``<>`` and ``()``."""
    fields: set[str] = set()
    depth = start = 0
    for i, ch in enumerate(ddl + ","):
        if ch in "<(":
            depth += 1
        elif ch in ">)":
            depth -= 1
        elif ch == "," and depth == 0:
            fields.add(ddl[start:i].split()[0].strip("`"))
            start = i + 1
    return fields


@pytest.mark.parametrize("resource_type", _LINKED_TYPES)
def test_each_resource_names_its_patient_in_a_field_of_its_type(resource_type):
    """SYNTH-MODULE: the element that holds the patient reference is one the type's resource struct has."""
    state = SolveState(resources=[ResourceSketch(resource_type=resource_type, template_id=None,
                                                 valueset_url=None, code_property=None, code=None)])
    _, resource = materialize(state, patient_id="p1", seed=1, patient_key="p1",
                              period_lo=dt.datetime(2026, 1, 1), period_hi=dt.datetime(2026, 12, 31))
    (link,) = [element for element, value in resource.items() if value == {"reference": "Patient/p1"}]
    assert link in _ddl_fields(_SCHEMAS[resource_type]["ddl"]), \
        f"{resource_type} names its patient in {link!r}, which its resource struct does not have"
