library FhirRetrieveRelation version '1.0.0'

// BIND-RELATION, BIND-CALL-SITE, BIND-RETRIEVE-REQUEST: each retrieve reads the relation its call site is
// bound to in the compile request. The harness binds every type by rule to `global_temp.<type>`, which holds
// Patient's rows and is an EMPTY decoy for every other type, and every non-Patient retrieve site through the map
// to a view of its own holding its type's rows. So a site that reads the rule instead of its map entry reads
// nothing, and the two Condition retrieves, in two defines, are two sites. A retrieve with a value set, one
// with a closed date window and a bare one each have a request of their own.
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
