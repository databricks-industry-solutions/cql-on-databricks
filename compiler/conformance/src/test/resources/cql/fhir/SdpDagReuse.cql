library SdpDagReuse version '1.0.0'

// PIPE-SQL-DAG-REUSE / MATERIALIZE-SINK sink flavor 4 (the SQL-back-end cross-dataset DAG cut). A
// two-define measure where a MATERIALIZED define references another MATERIALIZED sibling:
//   - "DiabetesList"  = [Condition: "Diabetes"]  -> a value-set retrieve, so the classifier materializes it;
//   - "Denominator"   = exists ("DiabetesList")  -> a population name, so it is materialized too.
// When the reference pipeline publishes "DiabetesList" as its own dataset (a cost-chosen boundary), the
// compiler must cut "Denominator"'s recipe at that boundary: it reads the published dataset
// (`FROM cql_DiabetesList`) instead of re-inlining the Condition retrieve. Same Diabetes value-set data as
// FhirValueSet: only p1 has a Diabetes-coded Condition, so "Denominator" is true for p1 and false for the rest.
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1' called FHIRHelpers

valueset "Diabetes": 'http://example.org/vs/diabetes'

context Patient

define "DiabetesList": [Condition: "Diabetes"]

define "Denominator": exists ("DiabetesList")
