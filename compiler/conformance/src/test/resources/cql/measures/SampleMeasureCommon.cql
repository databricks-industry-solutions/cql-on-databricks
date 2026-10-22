/*
@description: Shared logic for the sample quality measures: qualifying visits, age, the timing of conditions,
  procedures and observations, and the hospice and palliative care exclusions.
@intent: One place for the checks several sample measures make, so each measure reads as its own clinical logic.
@scope: QI-Core 6.0.0 resources. Written for this repository; not derived from any published measure library.
@guidance: The measurement period is passed in at run time. Value sets are the repository's own sample value sets.
*/
library SampleMeasureCommon version '1.0.0'

using QICore version '6.0.0'

include FHIRHelpers version '4.0.1' called FHIRHelpers

codesystem "ConditionClinicalStatusCodes": 'http://terminology.hl7.org/CodeSystem/condition-clinical'
codesystem "ConditionVerificationStatusCodes": 'http://terminology.hl7.org/CodeSystem/condition-ver-status'

valueset "Sample Outpatient Visit": 'http://example.org/fhir/ValueSet/sample-outpatient-visit'
valueset "Sample Hospice Care": 'http://example.org/fhir/ValueSet/sample-hospice-care'
valueset "Sample Discharge To Hospice": 'http://example.org/fhir/ValueSet/sample-discharge-to-hospice'
valueset "Sample Palliative Care": 'http://example.org/fhir/ValueSet/sample-palliative-care'

code "active": 'active' from "ConditionClinicalStatusCodes" display 'Active'
code "recurrence": 'recurrence' from "ConditionClinicalStatusCodes" display 'Recurrence'
code "relapse": 'relapse' from "ConditionClinicalStatusCodes" display 'Relapse'
code "refuted": 'refuted' from "ConditionVerificationStatusCodes" display 'Refuted'
code "entered-in-error": 'entered-in-error' from "ConditionVerificationStatusCodes" display 'Entered in Error'

parameter "Measurement Period" Interval<DateTime>

context Patient

/*
@description: The patient's age in whole years on the last day of the measurement period.
*/
define "Age At End Of Period":
  AgeInYearsAt(date from end of "Measurement Period")

/*
@description: Finished outpatient, telehealth or preventive visits that took place during the measurement period.
@guidance: The visit's period must fall within the measurement period, compared by day.
*/
define "Qualifying Encounters":
  [Encounter: "Sample Outpatient Visit"] Visit
    where Visit.status = 'finished'
      and Visit.period during day of "Measurement Period"

/*
@description: The patient received hospice care during the measurement period, or was discharged to hospice.
@guidance: Either a completed hospice care procedure that overlaps the period, or a finished encounter that
  ends in the period with a discharge disposition of hospice.
*/
define "In Hospice Care":
  exists ([Procedure: "Sample Hospice Care"] Hospice
    where Hospice.status = 'completed'
      and Hospice.performedPeriod() overlaps day of "Measurement Period")
    or exists ([Encounter] Discharge
      where Discharge.status = 'finished'
        and Discharge.hospitalization.dischargeDisposition in "Sample Discharge To Hospice"
        and end of Discharge.period during day of "Measurement Period")

/*
@description: The patient has a palliative care diagnosis that overlaps the measurement period.
*/
define "Has Palliative Care":
  exists (([ConditionEncounterDiagnosis: "Sample Palliative Care"]
    union [ConditionProblemsHealthConcerns: "Sample Palliative Care"]) Palliative
      where Palliative.isNotRefuted()
        and Palliative.prevalencePeriod() overlaps day of "Measurement Period")

/*
@description: The span over which a condition applies to the patient: from its onset to its abatement.
@guidance: The start is the onset (a date and time, or the start of an onset period), else the recorded date.
  The end is the abatement (a date and time, or the end of an abatement period). A condition with no
  abatement has no end, so it is still present.
*/
define fluent function prevalencePeriod(condition Choice<ConditionEncounterDiagnosis, ConditionProblemsHealthConcerns>):
  Interval[Coalesce(condition.onset as DateTime, start of (condition.onset as Interval<DateTime>), condition.recordedDate),
    Coalesce(condition.abatement as DateTime, end of (condition.abatement as Interval<DateTime>))]

/*
@description: The condition has not been refuted or entered in error. A condition with no verification status counts.
*/
define fluent function isNotRefuted(condition Choice<ConditionEncounterDiagnosis, ConditionProblemsHealthConcerns>):
  not (condition.verificationStatus ~ "refuted" or condition.verificationStatus ~ "entered-in-error")

/*
@description: The condition's clinical status is active, recurrence or relapse.
*/
define fluent function isActive(condition Choice<ConditionEncounterDiagnosis, ConditionProblemsHealthConcerns>):
  condition.clinicalStatus ~ "active"
    or condition.clinicalStatus ~ "recurrence"
    or condition.clinicalStatus ~ "relapse"

/*
@description: The span over which a procedure was performed: a single date and time, or a period.
*/
define fluent function performedPeriod(procedure Procedure):
  if procedure.performed is DateTime then
    Interval[procedure.performed as DateTime, procedure.performed as DateTime]
  else
    procedure.performed as Interval<DateTime>

/*
@description: The latest point of a blood pressure reading's effective time: the date and time itself, or the end
  of a period.
*/
define fluent function latestReading(observation USCoreBloodPressureProfile):
  if observation.effective is Interval<DateTime> then
    end of (observation.effective as Interval<DateTime>)
  else
    observation.effective as DateTime

/*
@description: The latest point of a laboratory result's effective time: the date and time itself, or the end of a
  period.
*/
define fluent function latestResult(observation LaboratoryResultObservation):
  if observation.effective is Interval<DateTime> then
    end of (observation.effective as Interval<DateTime>)
  else
    observation.effective as DateTime
