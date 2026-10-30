library FhirReferenceFollowValue version '1.0.0'

// ISOLATES the scalar reference-follow (LANG-REFERENCE-FOLLOW, VALUE form): a
// `singleton from ([Encounter] E where E.id = <ref>)` that is read for its VALUE (a field is navigated
// off the followed resource), NOT used as a `with`/`without` existence test. This is the
// `(Reading.encounter.encounterFor()).class.code` shape, isolated to a PLAIN-field navigation
// (Encounter.status) so it validates the correlated projecting LEFT JOIN on its own — the value cousin of
// FhirReferenceFollow's semi-join. (The `.class.code` value[x]/ToCode navigation is a separate concern
// exercised by the measure itself.)
//
// Each Observation carries `encounter.reference = Encounter/<id>`; encounterFor follows it to the single
// matching Encounter and reads its status: o1->e1(finished), o2->e1b(arrived), o3->e2(planned),
// o4->e4(finished). A wrong correlation would project the wrong (or no) encounter's status — the follow
// appears in a RETURN and in a WHERE so both clause positions are covered.
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

context Patient

define "FollowedEncounterStatuses":
  [Observation] O
    return ( O.encounter.encounterFor() ).status

define "ObservationsAtNonPlannedEncounters":
  [Observation] O
    where not ( ( O.encounter.encounterFor() ).status = 'planned' )
    return O.status

define fluent function encounterFor(reference Reference):
  singleton from ( [Encounter] E
      where E.id = Last(Split(reference.reference, '/'))
  )
