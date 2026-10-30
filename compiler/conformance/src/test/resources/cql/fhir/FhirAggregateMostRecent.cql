library FhirAggregateMostRecent version '1.0.0'

// LANG-AGGREGATE-FUNCTIONS: `Max` over the date-times a query over a retrieve returns, the "most recent" shape,
// inline and through a list define. p1 has two encounters (2022-01-01, 2023-03-01) -> 2023-03-01T08:00:00;
// p2, p3, p6 and p7 have one each; p4, p5 and p8 have none -> null.
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

define "Most Recent Encounter Start": Max([Encounter] E return start of E.period)

define "Encounter Starts": [Encounter] E return start of E.period

define "Most Recent Start Via Define": Max("Encounter Starts")
