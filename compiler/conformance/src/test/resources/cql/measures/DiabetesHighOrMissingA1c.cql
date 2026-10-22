/*
@description: Adults 18 to 75 with diabetes whose most recent HbA1c in the measurement period is above 9%, has
  no result, or who had no HbA1c test in the period.
@intent: A sample inverse proportion measure, written for this repository, in the shape CMS uses for FHIR quality
  measures. A missing test counts against the patient, so the numerator is the gap in care.
@scope: QI-Core 6.0.0. A diabetes diagnosis that overlaps the measurement period, and a qualifying outpatient
  visit in the period.
@nonGoal: Not a published measure. Glucose management indicator readings are not counted.
@guidance: Lower is better. The measurement period is passed in at run time.
*/
library DiabetesHighOrMissingA1c version '1.0.0'

using QICore version '6.0.0'

include FHIRHelpers version '4.0.1' called FHIRHelpers
include SampleMeasureCommon version '1.0.0' called Common

valueset "Sample Diabetes": 'http://example.org/fhir/ValueSet/sample-diabetes'
valueset "Sample HbA1c Lab Test": 'http://example.org/fhir/ValueSet/sample-hba1c-lab-test'

parameter "Measurement Period" Interval<DateTime>

context Patient

/*
@description: The patient has a diabetes diagnosis, not refuted, that overlaps the measurement period.
*/
define "Has Diabetes":
  exists (([ConditionProblemsHealthConcerns: "Sample Diabetes"]
    union [ConditionEncounterDiagnosis: "Sample Diabetes"]) Diabetes
      where Diabetes.isNotRefuted()
        and Diabetes.prevalencePeriod() overlaps day of "Measurement Period")

/*
@description: Patients 18 to 75 at the end of the period with diabetes and a qualifying visit in the period.
*/
define "Initial Population":
  Common."Age At End Of Period" in Interval[18, 75]
    and "Has Diabetes"
    and exists Common."Qualifying Encounters"

/*
@description: Everyone in the initial population.
*/
define "Denominator":
  "Initial Population"

/*
@description: Patients in hospice or palliative care during the period.
*/
define "Denominator Exclusions":
  Common."In Hospice Care"
    or Common."Has Palliative Care"

/*
@description: Final HbA1c tests taken during the measurement period, with or without a result.
*/
define "HbA1c Tests In Period":
  [LaboratoryResultObservation: "Sample HbA1c Lab Test"] Test
    where Test.status in { 'final', 'amended', 'corrected' }
      and Test.latestResult() during day of "Measurement Period"

/*
@description: The last test in the period: the one with no later test in the period.
@guidance: If two share the latest time, the one with the lower id is taken.
*/
define "Most Recent HbA1c":
  First("HbA1c Tests In Period" Test
    where not exists ("HbA1c Tests In Period" Later
      where Later.latestResult() after Test.latestResult())
    sort by id)

/*
@description: The most recent HbA1c result is above 9%.
*/
define "Most Recent HbA1c Above 9 Percent":
  ("Most Recent HbA1c".value as Quantity) > 9 '%'

/*
@description: The most recent HbA1c test has no result.
*/
define "Most Recent HbA1c Has No Result":
  "Most Recent HbA1c" is not null
    and "Most Recent HbA1c".value is null

/*
@description: The patient had no HbA1c test in the period.
*/
define "No HbA1c In Period":
  not exists "HbA1c Tests In Period"

/*
@description: Patients whose most recent HbA1c is above 9%, has no result, or who were not tested in the period.
*/
define "Numerator":
  "Most Recent HbA1c Above 9 Percent"
    or "Most Recent HbA1c Has No Result"
    or "No HbA1c In Period"
