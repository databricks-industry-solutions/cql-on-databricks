library FhirSubsetShape version '1.0.0'

// DATA-MODEL-SUBSET, DATA-SUBJECT-KEY, BIND-PREFILTER: the data is bound as views that return a subset of FHIR R4.
// Each view returns its `patient_id` as a root column, so no key is derived from `id` or a reference, and Patient
// returns `min_date` and `max_date`, both the birth date. Patient returns only `id`, `gender` and `birthDate` of its
// FHIR fields. Observation returns `id`, `status`, `subject`, and only two of the eleven `value[x]` slots,
// `valueQuantity` and `valueString`: no `valueCodeableConcept`, `valueRatio`, `valueSampledData`, ... and no
// `encounter` or `component`. The sample data holds values only in those two slots, so the answers equal the
// reference engine's over the full resources.
//
// Each define reads the choice through every slot the view holds and no other, so the SQL names no field the
// view leaves out: `is null` tests the two held slots; `is Ratio` is false, since a slot the view leaves out
// holds no value. o1 and o3 hold quantities (5 and 10 mg), o2 and o4 strings. So p1 has one quantity, not
// above 7 mg, and one string; p2 one quantity, above 7 mg; p3 one string; and no patient has an Observation
// without a value or with a ratio.
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

define "Female":
  Patient.gender = 'female'

define "Without A Value":
  exists ([Observation] O where O.value is null)

define "Quantity Count":
  Count([Observation] O where O.value is FHIR.Quantity)

define "Above 7 mg":
  exists ([Observation] O where (O.value as Quantity) > 7 'mg')

define "Text Count":
  Count([Observation] O where O.value is FHIR.string)

define "Has A Ratio":
  exists ([Observation] O where O.value is FHIR.Ratio)

// The retrieve's birth-date range pushes to the scan: `min_date` and `max_date` are the coarse filter, the birth-date
// test the refine step. p1 (1980) and p6 (1985) were born in the 1980s; p4 and p5 have no birth date and no dates.
define "Born In The 1980s":
  exists ([Patient] P where P.birthDate during Interval[@1980-01-01, @1989-12-31])
