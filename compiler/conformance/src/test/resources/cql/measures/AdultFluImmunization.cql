/*
@description: Adults seen during the flu season who received an influenza vaccine during that season.
@intent: A sample proportion measure, written for this repository, that shows denominator exceptions: a vaccine
  declined by the patient, or not given for a medical reason, is recorded as not done with its reason.
@scope: QI-Core 6.0.0. The flu season that ends in the measurement period: for a calendar-year period, August 1
  of the year before to March 31 of the period's year.
@nonGoal: Not a published measure. Children and earlier seasons are not counted.
@guidance: Higher is better. The measurement period is passed in at run time.
*/
library AdultFluImmunization version '1.0.0'

using QICore version '6.0.0'

include FHIRHelpers version '4.0.1' called FHIRHelpers
include SampleMeasureCommon version '1.0.0' called Common

valueset "Sample Influenza Vaccine": 'http://example.org/fhir/ValueSet/sample-influenza-vaccine'
valueset "Sample Patient Objection": 'http://example.org/fhir/ValueSet/sample-patient-objection'
valueset "Sample Medical Reason Not Given": 'http://example.org/fhir/ValueSet/sample-medical-reason-not-given'

parameter "Measurement Period" Interval<DateTime>

context Patient

/*
@description: The flu season that ends in a period: from five months before the period starts to three months
  after. For a calendar-year period, August 1 of the year before to March 31 of the period's year.
*/
define function "Flu Season"(period Interval<DateTime>):
  Interval[start of period - 5 months, start of period + 3 months)

/*
@description: Finished outpatient, telehealth or preventive visits during the flu season.
*/
define "Flu Season Encounters":
  [Encounter: Common."Sample Outpatient Visit"] Visit
    where Visit.status = 'finished'
      and Visit.period during day of "Flu Season"("Measurement Period")

/*
@description: Patients 18 or older at the start of the flu season with a qualifying visit during it.
*/
define "Initial Population":
  AgeInYearsAt(date from start of "Flu Season"("Measurement Period")) >= 18
    and exists "Flu Season Encounters"

/*
@description: Everyone in the initial population.
*/
define "Denominator":
  "Initial Population"

/*
@description: Patients in hospice care during the measurement period.
*/
define "Denominator Exclusions":
  Common."In Hospice Care"

/*
@description: Patients who received an influenza vaccine during the flu season.
*/
define "Numerator":
  exists ([Immunization: "Sample Influenza Vaccine"] Vaccine
    where Vaccine.status = 'completed'
      and (Vaccine.occurrence as DateTime) during day of "Flu Season"("Measurement Period"))

/*
@description: Patients whose influenza vaccine was not given during the season because they objected, or for a
  medical reason.
@guidance: The vaccine is recorded as not done, with the reason in its status reason.
*/
define "Denominator Exceptions":
  exists ([ImmunizationNotDone: "Sample Influenza Vaccine"] NotGiven
    where NotGiven.status = 'not-done'
      and (NotGiven.statusReason in "Sample Patient Objection"
        or NotGiven.statusReason in "Sample Medical Reason Not Given")
      and NotGiven.recorded during day of "Flu Season"("Measurement Period"))
