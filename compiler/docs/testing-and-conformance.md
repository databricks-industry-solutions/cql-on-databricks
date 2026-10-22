# Testing and conformance

The compiler is checked against ground truth, not assumed correct. Trust comes from matching the results
of a trusted reference.

The reference is the Java CQF engine (`org.cqframework:engine-jvm`), wired in `compiler/cqf` and run
in-process by `compiler/conformance` (`CONF-GROUND-TRUTH`).

## Gates — `CONF-GATES`

Four result gates. CI runs them with `-Dcqldatabricks.requireConnect=true`, so they fail rather than skip
if the Spark Connect server is missing.

| Gate | Test | What it checks |
|---|---|---|
| Per-operator (`CONF-GATE-OPERATOR`) | `OperatorTranslationSpec`, `OperatorConformanceSpec` | The vendored cqframework operator tests (`src/test/resources/cql-tests`) and hand cases, run as compiled SQL and compared with the reference |
| Property-based (`CONF-GATE-PROPERTY`) | `PropertyDifferentialSpec` | Generated expressions over nullable, interval and quantity values, with a fixed seed, compared with the reference |
| Whole-measure (`CONF-GATE-MEASURE`) | `SqlDifferentialSpec` and the corpus in `Corpus.scala` | Each fixture's compiled SQL, run with `spark.sql` on the optimized path, compared with the reference patient by patient |
| Scorekey (`CONF-GATE-SCOREKEY`) | `MeasureScoreSpec` | Each population define, per patient, reduced to 0 or 1 and compared with the measure's hand-written answer key |

`RefScoreSpec` (`CONF-REF-ANSWER-KEY`) runs the same answer-key comparison on the reference alone, with no
Spark, so a change to a measure's CQL, data or answer key is checked offline.

## Measure corpus

The measure gates discover the measures; none is listed in code (`CONF-MEASURE-DISCOVERY`). A measure is a
directory `src/test/resources/data/measures/<library>/` holding one `<Type>.ndjson` per FHIR resource type
and an `answer-key.json`. The answer key names the library, its version, its measurement period and its
populations (population define to population code), and gives, per patient, the value of each population
define and the reason. The CQL lives under `src/test/resources/cql/measures/`, and the include closure is
read from the translated ELM. Adding a measure is adding its CQL and that directory.

The measures are written for this repository, with synthetic test patients. Their CQL is a copy of the sample
measures in `examples/`, kept the same for now (`CONTENT-SAMPLE-PRELOAD`). The 48 hand-written patients are the
conformance corpus's own: `examples/` ships a synthesized population instead (`DATA-SYNTH-SMALL`). Their expected
results are worked out per patient and checked against the reference by `RefScoreSpec`.

| Measure | Includes | Patients |
|---|---|---|
| `HypertensionBPControl`: most recent blood pressure below 140/90 | `SampleMeasureCommon` | 48 |
| `DiabetesHighOrMissingA1c`: most recent HbA1c above 9% or missing | `SampleMeasureCommon` | 48 |
| `AdultFluImmunization`: vaccinated in the flu season, with exceptions | `SampleMeasureCommon` | 48 |
| `ColorectalScreening`: current on screening, per-test lookbacks | `SampleMeasureCommon` | 48 |

The measurement period is declared with no default. Both engines get it at run time as a parameter, from
the answer key (`LANG-PARAMETER`); no library's CQL is edited for a run.

The patterns a compiler fix or a plan shape depends on are small fixtures, not measures, so a measure can
change without losing them. The QICore fixtures over `src/test/resources/data/qicore/` cover a profile-only
retrieve (`FhirProfileOnlyRetrieve`), negation rationale (`FhirNegationRationale`), day-precision `during` of
an interval and of a point (`FhirDayPrecisionDuring`), age-coded onsets on the period boundaries
(`FhirAgeIntervalBoundaries`, with `AgeIntervalOracleSpec`), `value[x]` read off a define that holds one
resource (`FhirChoiceValueOffDefine`) and resources keyed by `beneficiary` or `patient`
(`FhirSubjectReferenceFields`). The six-arm disjunctive exclusion is `FhirDisjunctiveTemporalExists`, and the
decimal `aggregate` fold is `AggregateFold`. The aggregate functions are `AggregateFunctions`, and `Max` over a
retrieve query (the "most recent" shape) is `FhirAggregateMostRecent`. The value-set expansions the QICore
fixtures use are in `src/test/resources/data/valuesets/`.

`-Dcqldatabricks.skipMeasures=true` drops the measures from the executing gates for a fast focused run.

The vendored operator tests are five files: aggregate functions, arithmetic functions, comparison
operators, logical operators and null operators. Their source and licence are in `cql-tests/attribution.md`.

A wall-clock check also fails a fixture whose Spark run takes longer than
`-Dcqldatabricks.maxRuntimeMs` (default 10,000) at corpus size (`CONF-PERF-TIME`).

## Golden snapshots — `CONF-GOLDEN-PLAN`

Golden snapshots catch structural changes that keep results the same. `harness/Golden.scala` parses the
golden and the actual into `(depth, label)` lines and compares them. Whitespace and line endings don't
matter. Labels are case-sensitive.

The golden files are in `src/test/resources/golden/<fixture>/`. Today there are two, both IR trees:
`FhirNumerator.optimized.ir.txt` and `TrivialBoolean.ir.txt`. The emitted SQL is not snapshot-tested.

`-Dcqldatabricks.blessPlans=true` writes the new artifact to the dump directory, never to `src/`. Review it
and move it into the golden directory by hand.

The structural cost budget (`CONF-PERF-BUDGET`) is off: its baselines were removed. The code and the bless
path remain.

## Declared deviations — `CONF-REF-DEVIATION`

The reference is the oracle. A difference from it is allowed only when declared on the fixture with a
reason (`Differential.Case.deviations`). Examples:

- String equivalence normalizes whitespace, as the spec says. CQF 4.8.0 does not
  ([language-semantics.md](language-semantics.md) section 1.1).
- The order of an unsorted query result. Neither engine owes an order. `Differential.OrderOnly(define)`
  accepts only a permutation of the same elements, so a real value change still fails.
