library FhirTerminologyLayers version '1.0.0'

// BIND-PROVIDER-REQUEST, CLIENT-PROVIDER-OVERLAY: a value set is read from the FIRST terminology relation
// that holds it, so a development layer replaces a published set rather than merging with it. The harness
// gives the compile two layers. "Diabetes" (first by id) is in both: the first holds its expansion
// (73211009), the second every code of both sets. "Type Two" is only in the second (44054006).
//
// Data discriminates: p1 has both codes (c1, c2), p2 only 44054006 (c3). "Diabetic" is true for p1 alone;
// reading "Diabetes" from the second layer, or from both, makes p2 diabetic too.
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

valueset "Diabetes": 'http://example.org/vs/diabetes'
valueset "Type Two": 'http://example.org/vs/type-two'

context Patient

define "Diabetic":
  exists [Condition: "Diabetes"]

define "Has Type Two":
  exists [Condition: "Type Two"]
