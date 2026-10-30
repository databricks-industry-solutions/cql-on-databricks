library FhirProfileOnlyRetrieve version '1.0.0'

// LANG-RETRIEVE-PROFILE: a retrieve that names a PROFILE and no code. The profile is the only filter, so it
// must be tested at the scan against `meta.profile`: a resource that claims the profile is kept, one that
// claims only a different profile is dropped, and one that claims none is kept (a claim, not a guarantee).
// Without the filter the retrieve returns every Observation for the subject.
//
// DATA (`data/qicore/Observation.ndjson`): q1 claims the blood-pressure profile, q2 only the body-weight
// profile, q3 nothing, q4 both. So q1, q3 and q4 have a reading and q2 has none.
using QICore version '6.0.0'

include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

define "Blood Pressure Reading Ids":
  [USCoreBloodPressureProfile] Reading
    return Reading.id

define "Has Blood Pressure Reading":
  exists [USCoreBloodPressureProfile]
