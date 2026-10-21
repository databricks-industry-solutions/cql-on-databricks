library FhirAgeIntervalBoundaries version '1.0.0'

// LANG-CHOICE-MODEL / LANG-QUANTITY: a condition onset coded as an AGE. FHIR allows `Condition.onset` to
// be an `Age` (a calendar-duration Quantity) or an age `Range`, meaning the year of the patient's life at
// that age. The interval is then `Patient.birthDate + <age>`, with the calendar unit read from the DATA
// (UCUM 'a'), not a literal. This is the one place a value[x] member is a duration rather than a point.
//
// The data puts those intervals on the measurement period's boundaries, so an off-by-one on the age
// arithmetic, or an age arm that comes back NULL, flips a boolean rather than only re-dating a value.
//
// The QICore model is required: under `using FHIR` the onset stays a choice of FHIR types and `is DateTime`
// does not translate. QICore declares the onset as a choice of SYSTEM types, so the translator inserts the
// `FHIRHelpers.ToValue` conversion and the `As(Property(ToValue(C.onset), 'low'), Quantity)` node the
// range arm reads.
//
// DATA (`data/qicore/Condition.ndjson`, `Patient.birthDate` in parentheses), period 2026:
//   a1 (1986-01-01) onset age 40       -> [2026-01-01, 2027-01-01): starts on the period's first day
//   a2 (1985-12-31) onset age 40       -> [2025-12-31, 2026-12-31): starts the day before, overlaps
//   a3 (1987-01-01) onset age 40       -> [2027-01-01, 2028-01-01): starts the day after the period
//   a4 (1990-06-15) onset ages 30-35   -> [2020-06-15, 2026-06-15): overlaps, starts long before
//   a5 (no birth date) onset age 40    -> no interval
using QICore version '6.0.0'

include FHIRHelpers version '4.0.1' called FHIRHelpers

parameter "Measurement Period" Interval<DateTime>
  default Interval[@2026-01-01T00:00:00.000, @2026-12-31T23:59:59.999]

context Patient

define fluent function onsetSpan(condition ConditionProblemsHealthConcerns):
  if condition.onset is Quantity then
    Interval[ToDateTime(Patient.birthDate + (condition.onset as Quantity)),
      ToDateTime(Patient.birthDate + (condition.onset as Quantity) + 1 year))
  else if condition.onset is Interval<Quantity> then
    Interval[ToDateTime(Patient.birthDate + (condition.onset.low as Quantity)),
      ToDateTime(Patient.birthDate + (condition.onset.high as Quantity) + 1 year))
  else if condition.onset is DateTime then
    Interval[condition.onset as DateTime, condition.onset as DateTime]
  else null as Interval<DateTime>

define "Onset Spans":
  [ConditionProblemsHealthConcerns] Finding
    return Finding.onsetSpan()

define "Onset Starts In Period":
  exists ([ConditionProblemsHealthConcerns] Finding
    where start of Finding.onsetSpan() during day of "Measurement Period")

define "Onset Overlaps Period":
  exists ([ConditionProblemsHealthConcerns] Finding
    where Finding.onsetSpan() overlaps "Measurement Period")
