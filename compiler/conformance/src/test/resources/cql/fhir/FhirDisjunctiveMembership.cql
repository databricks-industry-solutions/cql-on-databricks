library FhirDisjunctiveMembership version '1.0.0'

// EXEC-* SQL backend, LANG-TERMINOLOGY: a value-set membership BURIED in an `or` — NOT a
// top-level `where` conjunct, so it cannot be a retrieve/frame semi-join. It must be LIFTED to a
// per-row boolean flag column (the SQL twin of `QueryLowering.liftMembershipFlags`), then referenced by
// the residual 3VL predicate. This is the `reasonCode in "VS" or …` shape, and was a SQL-backend
// blocker (`InValueSet` fell through
// `SqlColumnLowering` and failed loud). The membership shares the SAME `Diabetes` value set + measurement
// period + sample data as FhirExistsCompound, so the terminology expansion and date semantics are the
// proven ones; only the DISJUNCTIVE placement (forcing the flag lift over the pushdown/semi-join path)
// is new. The self-diff vs the Java CQF reference certifies the lift is result-equivalent (EXEC-SELFDIFF).
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

valueset "Diabetes": 'http://example.org/vs/diabetes'

parameter "Measurement Period" Interval<DateTime> default Interval[@2023-01-01T00:00:00, @2023-12-31T23:59:59]

context Patient

define "DiabeticOrInPeriod":
  [Condition] C
    where C.code in "Diabetes"
      or C.recordedDate during "Measurement Period"
    return C.subject.reference
