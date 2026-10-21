library FhirTemporalWhere version '1.0.0'

using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

parameter "Measurement Period" Interval<DateTime> default Interval[@2023-01-01T00:00:00, @2023-12-31T23:59:59]

context Patient

define "RecentConditionSubjects": [Condition] C where C.recordedDate during "Measurement Period" return C.subject.reference
