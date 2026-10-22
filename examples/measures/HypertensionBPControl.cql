/*
@description: Adults 18 to 85 with hypertension whose most recent blood pressure in the measurement period is
  below 140/90 mmHg.
@intent: A sample proportion measure, written for this repository, in the shape CMS uses for FHIR quality
  measures: an initial population, a denominator, exclusions, a numerator and supplemental data.
@scope: QI-Core 6.0.0. A hypertension diagnosis that starts on or before June 30 of the measurement period, and
  a qualifying outpatient visit in the period.
@nonGoal: Not a published measure. Readings are not reduced to the lowest of the day, and readings taken in an
  emergency or inpatient setting are not set aside.
@guidance: Higher is better. The measurement period is passed in at run time.
*/
library HypertensionBPControl version '1.0.0'

using QICore version '6.0.0'

include FHIRHelpers version '4.0.1' called FHIRHelpers
include SampleMeasureCommon version '1.0.0' called Common
include SampleSupplementalData version '1.0.0' called SDE

codesystem "LOINC": 'http://loinc.org'

valueset "Sample Essential Hypertension": 'http://example.org/fhir/ValueSet/sample-essential-hypertension'
valueset "Sample Kidney Failure": 'http://example.org/fhir/ValueSet/sample-kidney-failure'
valueset "Sample Dialysis Or Kidney Transplant": 'http://example.org/fhir/ValueSet/sample-dialysis-or-kidney-transplant'
valueset "Sample Pregnancy": 'http://example.org/fhir/ValueSet/sample-pregnancy'

code "Blood pressure panel": '85354-9' from "LOINC" display 'Blood pressure panel with all children optional'
code "Systolic blood pressure": '8480-6' from "LOINC" display 'Systolic blood pressure'
code "Diastolic blood pressure": '8462-4' from "LOINC" display 'Diastolic blood pressure'

parameter "Measurement Period" Interval<DateTime>

context Patient

/*
@description: The patient has hypertension, from the problem list or from an encounter and not refuted, that
  starts on or before June 30 of the measurement period and is still present during the period.
@guidance: The first six months give a newly diagnosed patient time to reach control before the period ends.
*/
define "Has Hypertension":
  exists (([ConditionProblemsHealthConcerns: "Sample Essential Hypertension"]
    union [ConditionEncounterDiagnosis: "Sample Essential Hypertension"]) Hypertension
    where Hypertension.isNotRefuted()
      and Hypertension.prevalencePeriod() overlaps day of "Measurement Period"
      and start of Hypertension.prevalencePeriod() before day of (start of "Measurement Period" + 6 months))

/*
@description: Patients 18 to 85 at the end of the period with hypertension and a qualifying visit in the period.
*/
define "Initial Population":
  Common."Age At End Of Period" in Interval[18, 85]
    and "Has Hypertension"
    and exists Common."Qualifying Encounters"

/*
@description: Everyone in the initial population.
*/
define "Denominator":
  "Initial Population"

/*
@description: Patients in hospice or palliative care, with kidney failure, or pregnant during the period.
*/
define "Denominator Exclusions":
  Common."In Hospice Care"
    or Common."Has Palliative Care"
    or "Has Kidney Failure"
    or "Pregnant During Period"

/*
@description: End-stage kidney disease, dialysis or a kidney transplant on or before the end of the period.
*/
define "Has Kidney Failure":
  exists (([ConditionProblemsHealthConcerns: "Sample Kidney Failure"]
    union [ConditionEncounterDiagnosis: "Sample Kidney Failure"]) KidneyFailure
      where KidneyFailure.isNotRefuted()
        and start of KidneyFailure.prevalencePeriod() on or before day of end of "Measurement Period")
    or exists ([Procedure: "Sample Dialysis Or Kidney Transplant"] KidneyProcedure
      where KidneyProcedure.status = 'completed'
        and start of KidneyProcedure.performedPeriod() on or before day of end of "Measurement Period")

/*
@description: A pregnancy diagnosis that overlaps the measurement period.
*/
define "Pregnant During Period":
  exists (([ConditionProblemsHealthConcerns: "Sample Pregnancy"]
    union [ConditionEncounterDiagnosis: "Sample Pregnancy"]) Pregnancy
      where Pregnancy.isNotRefuted()
        and Pregnancy.prevalencePeriod() overlaps day of "Measurement Period")

/*
@description: Final blood pressure readings taken during the measurement period that record both a systolic and
  a diastolic value.
*/
define "Blood Pressure Readings":
  [USCoreBloodPressureProfile: "Blood pressure panel"] Reading
    where Reading.status in { 'final', 'amended', 'corrected' }
      and Reading.latestReading() during day of "Measurement Period"
      and exists (Reading.component Systolic
        where Systolic.code ~ "Systolic blood pressure" and Systolic.value is not null)
      and exists (Reading.component Diastolic
        where Diastolic.code ~ "Diastolic blood pressure" and Diastolic.value is not null)

/*
@description: The last reading in the period: the one with no later reading in the period.
@guidance: If two share the latest time, the one with the lower id is taken.
*/
define "Most Recent Blood Pressure":
  First("Blood Pressure Readings" Reading
    where not exists ("Blood Pressure Readings" Later
      where Later.latestReading() after Reading.latestReading())
    sort by id)

/*
@description: The systolic value of the most recent reading.
*/
define "Most Recent Systolic":
  First("Blood Pressure Readings" Reading
    where Reading.id = "Most Recent Blood Pressure".id
    return First(Reading.component Systolic
      where Systolic.code ~ "Systolic blood pressure"
      return Systolic.value as Quantity))

/*
@description: The diastolic value of the most recent reading.
*/
define "Most Recent Diastolic":
  First("Blood Pressure Readings" Reading
    where Reading.id = "Most Recent Blood Pressure".id
    return First(Reading.component Diastolic
      where Diastolic.code ~ "Diastolic blood pressure"
      return Diastolic.value as Quantity))

/*
@description: The most recent systolic value is below 140 mmHg.
*/
define "Systolic Below 140":
  "Most Recent Systolic" < 140 'mm[Hg]'

/*
@description: The most recent diastolic value is below 90 mmHg.
*/
define "Diastolic Below 90":
  "Most Recent Diastolic" < 90 'mm[Hg]'

/*
@description: Patients whose most recent blood pressure in the period is below 140/90 mmHg.
@guidance: A patient with no reading in the period is not in the numerator.
*/
define "Numerator":
  "Systolic Below 140"
    and "Diastolic Below 90"

/*
@description: Supplemental data: administrative gender.
*/
define "SDE Sex":
  SDE."SDE Sex"

/*
@description: Supplemental data: the type of each coverage during the period.
*/
define "SDE Payer":
  SDE."SDE Payer"
