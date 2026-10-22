/*
@description: Helpers for the synthesizer's pattern fixture, written for its tests: an age define and the helper
  functions that read a FHIR choice element through an `if` and build an interval from an element's bounds. It
  mirrors no published library; every code is synthetic.
*/
library SynthesizerPatternsCommon version '1.0.0'

using QICore version '6.0.0'

include FHIRHelpers version '4.0.1' called FHIRHelpers

parameter "Measurement Period" Interval<DateTime>
  default Interval[@2026-01-01T00:00:00.000, @2026-12-31T23:59:59.999]

context Patient

define "Age At End Of Period":
  AgeInYearsAt(date from end of "Measurement Period")

define fluent function performedPeriod(procedure Procedure):
  if procedure.performed is DateTime then
    Interval[procedure.performed as DateTime, procedure.performed as DateTime]
  else
    procedure.performed as Interval<DateTime>

define fluent function prevalencePeriod(condition ConditionProblemsHealthConcerns):
  Interval[Coalesce(condition.onset as DateTime, start of (condition.onset as Interval<DateTime>), condition.recordedDate),
    Coalesce(condition.abatement as DateTime, end of (condition.abatement as Interval<DateTime>))]

define fluent function latestResult(observation LaboratoryResultObservation):
  if observation.effective is Interval<DateTime> then
    end of (observation.effective as Interval<DateTime>)
  else
    observation.effective as DateTime
