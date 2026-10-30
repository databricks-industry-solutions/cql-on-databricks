/*
@description: The synthesizer's own test measure, written for its tests: adults aged 30 to 70 with a pilot
  condition and a finished pilot visit in the period, a pilot exclusion procedure, and a final pilot lab result.
  It mirrors no published measure; every code is synthetic.
*/
library SynthesizerPilot version '1.0.0'

using QICore version '6.0.0'

include FHIRHelpers version '4.0.1' called FHIRHelpers

codesystem "ConditionVerification": 'http://terminology.hl7.org/CodeSystem/condition-ver-status'

valueset "Pilot Visit": 'http://example.org/fhir/ValueSet/cql-test-pilot-visit'
valueset "Pilot Condition": 'http://example.org/fhir/ValueSet/cql-test-pilot-condition'
valueset "Pilot Exclusion Procedure A": 'http://example.org/fhir/ValueSet/cql-test-pilot-exclusion-a'
valueset "Pilot Exclusion Procedure B": 'http://example.org/fhir/ValueSet/cql-test-pilot-exclusion-b'
valueset "Pilot Lab Test": 'http://example.org/fhir/ValueSet/cql-test-pilot-lab-test'

code "Confirmed": 'confirmed' from "ConditionVerification"
code "Unconfirmed": 'unconfirmed' from "ConditionVerification"
code "Provisional": 'provisional' from "ConditionVerification"
code "Differential": 'differential' from "ConditionVerification"

parameter "Measurement Period" Interval<DateTime>
  default Interval[@2026-01-01T00:00:00.000, @2026-12-31T23:59:59.999]

context Patient

define "Age In Range":
  AgeInYearsAt(date from end of "Measurement Period") between 30 and 70

define "Qualifying Visits":
  [Encounter: "Pilot Visit"] Visit
    where Visit.status = 'finished'
      and Visit.period during "Measurement Period"

define "Has Pilot Diagnosis":
  exists ([ConditionProblemsHealthConcerns: "Pilot Condition"] Diagnosis
    where Diagnosis.verificationStatus is not null implies
      (Diagnosis.verificationStatus ~ "Confirmed"
        or Diagnosis.verificationStatus ~ "Unconfirmed"
        or Diagnosis.verificationStatus ~ "Provisional"
        or Diagnosis.verificationStatus ~ "Differential"))

define "Initial Population":
  "Age In Range"
    and exists "Qualifying Visits"
    and "Has Pilot Diagnosis"

define "Denominator":
  "Initial Population"

define "Pilot Exclusion Procedures":
  ([Procedure: "Pilot Exclusion Procedure A"] union [Procedure: "Pilot Exclusion Procedure B"]) Done
    where Done.status = 'completed'

// A route the solver cannot invert (it has no inverse for `Count`), beside one it can: the exclusion holds
// through either arm, so a solve that needs it true skips this one and reports the skip (SYNTH-PDS-SKIPPED).
define "Repeated Exclusion Procedure":
  exists ([Procedure: "Pilot Exclusion Procedure B"] Done
    where Count(Done.reasonCode) > 2)

define "Denominator Exclusions":
  exists "Pilot Exclusion Procedures"
    or "Repeated Exclusion Procedure"

define "Numerator":
  exists ([LaboratoryResultObservation: "Pilot Lab Test"] Result
    where Result.status = 'final'
      and (Result.effective as DateTime) during "Measurement Period")
