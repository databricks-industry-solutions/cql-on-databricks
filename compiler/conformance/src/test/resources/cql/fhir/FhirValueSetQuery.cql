library FhirValueSetQuery version '1.0.0'

// LANG-TERMINOLOGY, LANG-QUERY: a value-set-filtered retrieve used as a general QUERY
// SOURCE — not the bare-`exists` fast path, but a `return` query that routes through the Relation
// realization (QueryLowering.aliasedSource). This is exactly the shape the terminology pushdown
// optimizer emits (`[Resource: "VS"]` into a query source), so its row-level membership must lower:
// keep only Conditions whose code is in the Diabetes value set (a broadcast semi-join at the row
// level), then return each surviving Condition's id. Discriminated by the sample data: p1 has one
// Diabetes Condition (c2) AND one non-Diabetes Condition (c1, which must be filtered OUT), p2 has
// only a non-Diabetes Condition (empty list), p3 has none (empty list). The return projects
// `C.subject.reference` (a stable string both sides render identically — sidestepping the FHIR
// `.id` qualification quirk, an orthogonal lineage concern): if filtering were broken and
// kept c1 too, p1's list would have TWO elements, so the value still proves the row-level filter.
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

valueset "Diabetes": 'http://example.org/vs/diabetes'

context Patient

define "DiabetesConditionSubjects": [Condition: "Diabetes"] C return C.subject.reference
