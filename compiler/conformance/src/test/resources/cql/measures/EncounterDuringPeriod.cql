/*
@description: Whether a patient had any encounter during the measurement period.
@intent: A reusable check that a patient was seen in the period, for measures that include it.
@scope: Encounters of any status and any type.
@guidance: The measurement period is passed in at run time.
*/
library EncounterDuringPeriod version '1.0.0'

using FHIR version '4.0.1'

include FHIRHelpers version '4.0.1' called FHIRHelpers

parameter "Measurement Period" Interval<DateTime>

context Patient

/*
@description: The patient has at least one encounter whose period falls within the measurement period.
@guidance: Both ends of the encounter must be inside the period; the period's own bounds are inclusive. An
  encounter with no period does not count.
*/
define "Has Encounter During Period":
  exists ([Encounter] Visit
    where Visit.period during "Measurement Period")
