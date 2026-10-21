/*
@description: Patients with an encounter during the measurement period who have any condition on record.
@intent: The smallest complete measure: two checks and an initial population, to show how a measure runs.
@scope: Encounters and conditions of any status and any type.
@nonGoal: Has no denominator, numerator or exclusions.
@guidance: The measurement period is passed in at run time.
*/
library BasicEncounterAndCondition version '1.0.0'

using FHIR version '4.0.1'

include FHIRHelpers version '4.0.1' called FHIRHelpers
include EncounterDuringPeriod version '1.0.0' called Encounters

parameter "Measurement Period" Interval<DateTime>

context Patient

/*
@description: The patient has at least one encounter during the measurement period.
@guidance: Read from the included EncounterDuringPeriod library.
*/
define "Has Encounter During Period":
  Encounters."Has Encounter During Period"

/*
@description: The patient has at least one condition on record.
@guidance: Any condition counts, whatever its status or date.
*/
define "Has Any Condition":
  exists [Condition]

/*
@description: Patients who had an encounter during the period and have any condition.
*/
define "Initial Population":
  "Has Encounter During Period"
    and "Has Any Condition"
