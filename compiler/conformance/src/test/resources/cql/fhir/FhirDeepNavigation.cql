library FhirDeepNavigation version '1.0.0'

// ISOLATES navigation the old five-level depth cap dropped (DATA-MODEL-SCHEMA, DATA-MODEL-CUTS): the data model
// keeps every element at any depth except at a loop cut.
//
// - "RaceCategories" reads a value from an extension nested one level inside a resource extension. p1 has two
//   categories (R1, R2) and a text entry; p2 has one (R3) beside an unrelated extension; the rest have no race
//   extension. So p1->[R1, R2], p2->[R3], others->null.
// - "DoseBoundsLowAbove3Days" reads `dosageInstruction.timing.repeat.boundsRange.low`, five levels below the
//   resource, and compares it with 3 days. m1 (p1) is 2 days, m2 (p2) is 5 days; the other requests have no
//   dosage. So p1->[false], p2->[true], others->[]. (The comparison avoids returning a bare FHIR decimal.)
// - "AssignerNames" reads `identifier.assigner.display` through the cut edge: p1's data also carries
//   `assigner.identifier`, which the data model leaves out and the read drops. p1->['Example Hospital'].
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

define "RaceCategories":
  (singleton from (Patient.extension E where E.url = 'http://example.org/fhir/StructureDefinition/race')) R
    return (R.extension X where X.url = 'category' return (X.value as Coding).code)

define "DoseBoundsLowAbove3Days":
  [MedicationRequest] M
    where exists M.dosageInstruction
    return ((singleton from M.dosageInstruction).timing.repeat.bounds as Range).low > 3 'd'

define "AssignerNames":
  Patient.identifier I return I.assigner.display
