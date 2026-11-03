library FhirBareRetrieveList version '1.0.0'

// DATA-DEFINE-DF, DATA-SKIP-UNFILTERED foundation: a BARE unfiltered-retrieve
// define — the canonical `define E: [Encounter]` measure authors write. It is realized as a Relation
// frame (rows + __ordinal) that is a VIEW over the source scan, not a collect_list aggregation. The
// raw-resource result exports by its identity, its declared type and its own id (DATA-EXPORT). This is the foundation step 2 (DATA-SKIP-UNFILTERED) builds on: once E is a view,
// a downstream consumer's filter folds per-consumer onto the scan.
//
// Data discriminates: p1 has two encounters (e1, e1b) — order + multiplicity, p2 one (e2), p3 one (e4).
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

define "AllEncounters": [Encounter]
