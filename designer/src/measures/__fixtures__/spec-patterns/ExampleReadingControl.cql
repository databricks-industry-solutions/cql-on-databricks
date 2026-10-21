/*
@description: Patients with an example condition whose lowest readings on their most recent reading day are below a
  threshold.
@intent: A Designer test fixture (APP-SPEC-MODEL), not a sample measure. Each define gives a Designer test a shape
  to read: a read issued against two profiles, a define reached from two branches, a function with its own read,
  a code compared in a where clause rather than pushed into the retrieve, and a define nothing reaches.
@guidance: Synthetic codes and value sets only. Edit this file, then run designer/scripts/build-ui-fixtures.sh.
*/
library ExampleReadingControl version '1.0.0'

using QICore version '6.0.0'

include FHIRHelpers version '4.0.1' called FHIRHelpers

codesystem "CqlTest": 'http://example.org/fhir/CodeSystem/cql-test'

valueset "Example Condition Diagnoses": 'http://example.org/fhir/ValueSet/example-condition-diagnoses'
valueset "Example Exclusion Conditions": 'http://example.org/fhir/ValueSet/example-exclusion-conditions'
valueset "Example Unreached Conditions": 'http://example.org/fhir/ValueSet/example-unreached-conditions'
valueset "Example Office Visit": 'http://example.org/fhir/ValueSet/example-office-visit'
valueset "Example Excluding Encounter": 'http://example.org/fhir/ValueSet/example-excluding-encounter'

code "Example Reading A": 'example-reading-a' from "CqlTest" display 'Example reading A'
code "Example Reading B": 'example-reading-b' from "CqlTest" display 'Example reading B'

parameter "Measurement Period" Interval<DateTime>

context Patient

define "Example Condition Diagnosis":
  [ConditionProblemsHealthConcerns: "Example Condition Diagnoses"]
    union [ConditionEncounterDiagnosis: "Example Condition Diagnoses"]

define "Has Example Condition":
  exists "Example Condition Diagnosis"

define "Visits During Period":
  [Encounter: "Example Office Visit"] Visit
    where Visit.status = 'finished'
      and Visit.period during "Measurement Period"

define "Has Visit During Period":
  exists "Visits During Period"

define "Initial Population":
  "Has Example Condition"
    and "Has Visit During Period"

define "Denominator":
  "Initial Population"

define "Exclusion Condition":
  [ConditionProblemsHealthConcerns: "Example Exclusion Conditions"]
    union [ConditionEncounterDiagnosis: "Example Exclusion Conditions"]

define "Has Exclusion Condition":
  exists "Exclusion Condition"

define "Has Excluding Encounter":
  exists ([Encounter: "Example Excluding Encounter"] Stay
    where Stay.period overlaps "Measurement Period")

define "Denominator Exclusions":
  "Has Exclusion Condition"
    or "Has Excluding Encounter"

define function "visitsFor"(reading SimpleObservation):
  [Encounter: "Example Office Visit"] Visit
    where reading.encounter.reference = 'Encounter/' + Visit.id

define "Qualifying Reading":
  [SimpleObservation] Reading
    where Reading.status in { 'final', 'amended', 'corrected' }
      and (Reading.effective as DateTime) during "Measurement Period"
      and exists "visitsFor"(Reading)

define "Most Recent Reading Day":
  Max("Qualifying Reading" Reading
    return date from (Reading.effective as DateTime))

define "Lowest Reading A On Most Recent Day":
  Min("Qualifying Reading" Reading
    where date from (Reading.effective as DateTime) = "Most Recent Reading Day"
      and Reading.code ~ "Example Reading A"
    return (Reading.value as Quantity).value)

define "Lowest Reading B On Most Recent Day":
  Min("Qualifying Reading" Reading
    where date from (Reading.effective as DateTime) = "Most Recent Reading Day"
      and Reading.code ~ "Example Reading B"
    return (Reading.value as Quantity).value)

define "Has Reading A Below Threshold":
  "Lowest Reading A On Most Recent Day" < 100

define "Has Reading B Below Threshold":
  "Lowest Reading B On Most Recent Day" < 60

define "Numerator":
  "Has Reading A Below Threshold"
    and "Has Reading B Below Threshold"

define "Unreached Condition":
  [ConditionProblemsHealthConcerns: "Example Unreached Conditions"]
    union [ConditionEncounterDiagnosis: "Example Unreached Conditions"]
