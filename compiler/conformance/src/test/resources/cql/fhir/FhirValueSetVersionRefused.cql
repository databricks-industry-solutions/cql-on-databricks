library FhirValueSetVersionRefused version '1.0.0'

// LANG-VALUESET-VERSION: a membership that cannot read exactly one expansion fails, naming the value set, instead of
// answering. Each define is one refusal: "versioned" holds versions '1' and '2' and is declared with none; no
// relation holds "absent"; "diabetes" holds no version '9'. ValueSetVersionSpec checks each message.
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

valueset "Two Versions": 'http://example.org/vs/versioned'
valueset "Absent": 'http://example.org/vs/absent'
valueset "Diabetes Nine": 'http://example.org/vs/diabetes' version '9'

context Patient

define "Unpinned":
  exists [Condition: "Two Versions"]

define "Missing":
  exists [Condition: "Absent"]

define "Pinned Nine":
  exists [Condition: "Diabetes Nine"]
