library ConceptResolution version '1.0.0'

// transformation-catalog.md section 5 (LANG-TERMINOLOGY): a NORMALIZE-stage transform. CQL terminology
// promotions resolve to LITERAL ConceptValue kernel nodes in Normalize (ConceptResolver), so no runtime
// terminology service is consulted at lowering. A standalone `concept` reference (ConceptRef) folds to
// a literal `ConceptValue` carrying its `(system, code)` pairs; after Normalize the library carries
// ConceptValue nodes and NO unresolved ConceptRef.
//
// This is a COMPILE-ONLY fixture (not in the executable Corpus): a scalar concept value has no lowering
// — the engine only lowers a ConceptValue as the RHS of `<FHIR concept> ~ ConceptValue` (needs FHIR
// data). Its validation is the IR assertion in TransformationCatalogSpec section 5.

codesystem "SNOMED": 'http://snomed.info/sct'
code "Confirmed": '410605003' from "SNOMED" display 'Confirmed'
concept "ConfirmedConcept": { "Confirmed" } display 'Confirmed'

define "TheConcept": "ConfirmedConcept"   // ConceptRef -> literal ConceptValue in Normalize
