library FhirDateArithmetic version '1.0.0'

// Minimal end-to-end isolation of the temporal-arithmetic TOWER behind an exclusion of the form
// `<procedure>.performed.performedSpan() ends on or before end of "Measurement Period"`. The
// value-set / choice / `performedSpan` / union machinery around it is stripped as superfluous; what
// remains is the essential offending shape that a literal-only fixture would constant-fold away:
//   - a RUNTIME date operand + a calendar Quantity  ->  the `make_interval`/`date_format` tower
//     (the birthDate+age arithmetic the Range branch of `performedSpan` performs, `dateArith`), and
//   - `end of` a half-open parameter Interval        ->  the boundary `addUnit` ladder + `date_format`.
// This is the `__h0..__h5` shape that surfaced the `date_format` pattern escaping bug and that the
// `(timestamp, precision)` companion-field optimization targets. LANG-INTERVAL / ARCH-TEMPORAL-CSE.
//
// Reuses the shared data/fhir Patient.ndjson (no new data). Discriminates over the shared birthDates:
//   1967-01-19 (+40y = 2007 -> <= end MP -> TRUE) ; 1990-12-31 (+40y = 2030 -> FALSE) ; 2008-* (FALSE).

using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

parameter "Measurement Period" Interval<DateTime>
  default Interval[@2026-01-01T00:00:00.0, @2027-01-01T00:00:00.0)

context Patient

// Per-patient boolean: does birthDate + 40 years land on or before the (half-open) end of the MP?
// `P.birthDate + 40 years` -> the make_interval/date_format tower; `end of "Measurement Period"` ->
// the boundary predecessor tower. Mirrors `... ends on or before end of "Measurement Period"`.
define "BirthPlus40BeforeMPEnd":
  exists ([Patient] P where (P.birthDate + 40 years) <= end of "Measurement Period")
