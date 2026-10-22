/*
@description: The synthesizer's pattern fixture, written for its tests: one define per CQL pattern the solver
  inverts, so each can be solved true and false on its own. It is not a measure and mirrors no published one;
  every code is synthetic.
*/
library SynthesizerPatterns version '1.0.0'

using QICore version '6.0.0'

include FHIRHelpers version '4.0.1' called FHIRHelpers
include SynthesizerPatternsCommon version '1.0.0' called Common

codesystem "LOINC": 'http://loinc.org'
codesystem "ConditionVerificationStatusCodes": 'http://terminology.hl7.org/CodeSystem/condition-ver-status'

valueset "Patterns Procedure": 'http://example.org/fhir/ValueSet/cql-test-patterns-procedure'
valueset "Patterns Condition": 'http://example.org/fhir/ValueSet/cql-test-patterns-condition'
valueset "Patterns Visit": 'http://example.org/fhir/ValueSet/cql-test-patterns-visit'
valueset "Patterns Discharge Disposition": 'http://example.org/fhir/ValueSet/cql-test-patterns-disposition'
valueset "Patterns Vaccine": 'http://example.org/fhir/ValueSet/cql-test-patterns-vaccine'
valueset "Patterns Reason Not Given": 'http://example.org/fhir/ValueSet/cql-test-patterns-reason'
valueset "Patterns Lab Test": 'http://example.org/fhir/ValueSet/cql-test-patterns-lab-test'

code "refuted": 'refuted' from "ConditionVerificationStatusCodes" display 'Refuted'
code "Blood pressure panel": '85354-9' from "LOINC" display 'Blood pressure panel with all children optional'
code "Systolic blood pressure": '8480-6' from "LOINC" display 'Systolic blood pressure'
code "Diastolic blood pressure": '8462-4' from "LOINC" display 'Diastolic blood pressure'

parameter "Measurement Period" Interval<DateTime>
  default Interval[@2026-01-01T00:00:00.000, @2026-12-31T23:59:59.999]

context Patient

// An age read through a define of an included library, tested against an interval.
define "Age Through A Define":
  Common."Age At End Of Period" in Interval[30, 70]

// A choice element read through an `if … is DateTime` helper that builds an interval of one point.
define "Procedure In Period":
  exists ([Procedure: "Patterns Procedure"] Done
    where Done.status = 'completed'
      and Done.performedPeriod() overlaps day of "Measurement Period")

// An interval from onset to abatement, open-ended because a condition carries no abatement.
define "Condition Present In Period":
  exists ([ConditionProblemsHealthConcerns: "Patterns Condition"] Diagnosis
    where Diagnosis.prevalencePeriod() overlaps day of "Measurement Period")

// A verification status that must not be refuted.
define "Condition Not Refuted":
  exists ([ConditionProblemsHealthConcerns: "Patterns Condition"] Diagnosis
    where not (Diagnosis.verificationStatus ~ "refuted"))

// A backbone element's field.
define "Discharged With Disposition":
  exists ([Encounter] Discharge
    where Discharge.hospitalization.dischargeDisposition in "Patterns Discharge Disposition")

// A retrieve kept empty while a resource of it exists: the visit stays, and fails the discharge's filter.
define "Visit With No Disposition":
  exists ([Encounter: "Patterns Visit"] Visit
    where Visit.status = 'finished'
      and Visit.period during day of "Measurement Period")
    and not exists ([Encounter] Discharge
      where Discharge.status = 'finished'
        and Discharge.hospitalization.dischargeDisposition in "Patterns Discharge Disposition")

// A component demanded present by a bare `exists`, beside one whose value is compared.
define "Readings":
  [USCoreBloodPressureProfile: "Blood pressure panel"] Reading
    where exists (Reading.component Systolic
        where Systolic.code ~ "Systolic blood pressure" and Systolic.value is not null)
      and exists (Reading.component Diastolic
        where Diastolic.code ~ "Diastolic blood pressure" and Diastolic.value is not null)

define "Systolic Below 140":
  First("Readings" Reading
    return First(Reading.component Systolic
      where Systolic.code ~ "Systolic blood pressure"
      return Systolic.value as Quantity)) < 140 'mm[Hg]'

// A resource's own value element, read off a define.
define "Latest Lab":
  First([LaboratoryResultObservation: "Patterns Lab Test"] Lab
    where Lab.latestResult() during day of "Measurement Period")

define "Latest Lab Has No Result":
  "Latest Lab" is not null
    and "Latest Lab".value is null

// An immunization not given, with its reason and the date it was recorded.
define "Vaccine Not Given":
  exists ([ImmunizationNotDone: "Patterns Vaccine"] NotGiven
    where NotGiven.status = 'not-done'
      and NotGiven.statusReason in "Patterns Reason Not Given"
      and NotGiven.recorded during day of "Measurement Period")
