library FhirRetrieveRelation version '1.0.0'

// BIND-RELATION, BIND-NAMED-ARGS, BIND-RETRIEVE-REQUEST: each retrieve reads the relation bound to its id
// in the compile request (BIND-PROVIDER-REQUEST). The harness binds Patient, the context type, to a table
// and every other type to a SQL function that declares four request fields by name, in another order than
// the request's, and keeps only rows whose `fhir_type` argument names its type. A retrieve with a value set,
// one with a closed date window and a bare one each pass different arguments; a positional or mis-named
// argument fails the call, and a wrong `fhir_type` returns no rows.
//
// Data discriminates: only p1 has a Condition in "Diabetes" (c2); p1, p5 and p6 have a Condition recorded
// on a day in 2023; p1 and p3 are born in the window, p2 is not.
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

valueset "Diabetes": 'http://example.org/vs/diabetes'

context Patient

define "Diabetic":
  exists [Condition: "Diabetes"]

define "Recorded In 2023":
  exists ([Condition] C where C.recordedDate during Interval[@2023-01-01T00:00:00, @2023-12-31T23:59:59])

define "Has Encounter":
  exists [Encounter]

define "Born In Range":
  exists ([Patient] P where P.birthDate during Interval[@1980-01-01, @1990-12-31])
