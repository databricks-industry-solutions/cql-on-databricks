library FhirValueSetVersion version '1.0.0'

// LANG-VALUESET-VERSION: a declaration that pins a version reads only that version's codes. The value set holds
// version '1' (73211009) and version '2' (44054006). p1 has both codes, p2 only 44054006, so "Pinned One" is true
// for p1 alone; reading every version makes p2 true too.
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

valueset "Versioned One": 'http://example.org/vs/versioned' version '1'

context Patient

define "Pinned One":
  exists [Condition: "Versioned One"]
