# Requirements Registry

The single index of every referenceable requirement. IDs are mnemonic and decoupled from document section numbers. The one-line statement here is the index entry; the full claim and rejected alternatives live in the linked source. Cite ids per `PROC-CITE-REQ`. When you add or change a requirement, update both its topic doc and this table.

## CORE — non-negotiable product invariants

| id | statement | source |
|----|-----------|--------|
| `CORE-FAITHFULNESS` | Faithfulness over speed; a fast wrong answer fails certification. | `overview.md` |
| `CORE-NO-SILENT-MISLOWER` | Unsupported constructs fail loudly at compile time, never a plausible-but-wrong plan. | `overview.md` |
| `CORE-DETERMINISM` | Same ELM + data + engine version ⇒ identical output. | `overview.md` |
| `CORE-ISOLATE-ANOMALY` | One patient's data anomaly never fails the batch (per-row results per spec). | `overview.md` |

## LANG — CQL semantics the engine must honor

| id | statement | source |
|----|-----------|--------|
| `LANG-3VL` | Every boolean-producing operator routes through three-valued logic; no bare Spark booleans. | `language-semantics.md` |
| `LANG-EQUIV` | `~` normalizes per type in ONE place; String normalizes case and whitespace per character. `distinct` and list `in` use EQUALITY, not equivalence. | `language-semantics.md` |
| `LANG-LIST` | CQL lists are ordered and allow duplicates; default `Relation` encoding carries `__ordinal`. | `language-semantics.md` |
| `LANG-LIST-NULL` | Empty vs null list conflated for now as a tested invariant, with a reserved escape hatch. | `language-semantics.md` |
| `LANG-LIST-NULL-STATE` | A list's null/empty/non-empty state is distinguished only for observing operators; every other consumer conflates null and empty. | `language-semantics.md` |
| `LANG-INTERVAL` | Precision-aware temporal comparison and full interval (Allen-relation) algebra. | `language-semantics.md` |
| `LANG-INTERVAL-PRECISION` | An Allen relation naming a precision truncates both operands to it before comparing. | `language-semantics.md` |
| `LANG-PARAM-INSTANT` | A parameter override binds at the precision its declared type calls for. | `language-semantics.md` |
| `LANG-PARAMETER` | CQL parameters bind to declared defaults or explicit overrides; the compiler inlines bound values as literals. | `language-semantics.md` |
| `LANG-QUANTITY` | Quantities are `(value, unit)` structs; arithmetic/comparison require UCUM conversion. | `language-semantics.md` |
| `LANG-INSTANCE` | ELM `Instance` types as their declared `classType` lowered to matching canonical structs. | `language-semantics.md` |
| `LANG-TERMINOLOGY` | Value-set membership is a broadcast/bloom semi-join over pre-expanded tables; expansion is external. | `language-semantics.md` |
| `LANG-CHOICE` | `Choice<…>` is a discriminated struct mirroring FHIR `value[x]`; first-class everywhere. | `language-semantics.md` |
| `LANG-CHOICE-MODEL` | `value[x]` slot projection/test fires only on genuine `[x]` choice elements, not any FHIR-typed property. | `language-semantics.md` |
| `LANG-CHOICE-RESOURCE` | A heterogeneous `Choice` may range over distinct FHIR resource bodies (union aligned into one Choice-tagged relation). | `language-semantics.md` |
| `LANG-CONTEXT` | Context maps to a grouping key; context is extensible, not hardcoded. | `language-semantics.md` |
| `LANG-CONTEXT-DEFAULT` | Spec-conformant context resolution: explicit request context, else library's declared context, else model's default, else `Unfiltered`. | `language-semantics.md` |
| `LANG-RUNTIME-COND` | CQL runtime conditions become per-row results/flags per spec, never job failures. | `language-semantics.md` |
| `LANG-QUERY` | The query construct lowers to set-at-a-time ops over `Relation`-encoded lists with `__ordinal` preserved. | `language-semantics.md` |
| `LANG-QUERY-WHERE` | `where` routes through 3VL; a null predicate excludes the row. | `language-semantics.md` |
| `LANG-QUERY-DEFREF` | A query clause may reference a sibling per-subject scalar define via LEFT-JOIN keyed by `__subject_id`. | `language-semantics.md` |
| `LANG-QUERY-WITH` | `with`/`without` are semi-/anti-joins with a 3VL `such that`, never row UDFs. | `language-semantics.md` |
| `LANG-REFERENCE-FOLLOW` | A `with`/`without` over a context-less resource correlates on reference equality alone. | `language-semantics.md` |
| `LANG-REFERENCE-FOLLOW-VALUE` | A reference-follow used for its value lowers to a correlated LEFT OUTER JOIN projecting the followed resource per outer row. | `language-semantics.md` |
| `LANG-QUERY-RETURN` | `return`/`distinct` shape results; distinct dedupes using EQUALITY semantics. | `language-semantics.md` |
| `LANG-QUERY-SORT` | `sort` authoritatively rewrites `__ordinal`; sort keys honor CQL ordering semantics. | `language-semantics.md` |
| `LANG-QUERY-AGGREGATE` | `aggregate … starting` folds in `__ordinal` order; CQL's bounded-recursion mechanism. | `language-semantics.md` |
| `LANG-QUERY-MULTISOURCE` | Multiple sources form a relational combination keyed by `__subject_id`; aliases enter scope. | `language-semantics.md` |
| `LANG-QUERY-LET` | A query `let` introduces named values in query-iteration scope, lowered by binding once via compute-once carrier or inlining. | `language-semantics.md` |
| `LANG-FUNCTION` | Non-recursive functions are inlined in Normalize; overload/fluent dispatch is resolved upstream. | `language-semantics.md` |
| `LANG-FUNCTION-TERMINOLOGY` | Value-set/code/concept arguments substitute the resolved terminology handle through inlining. | `language-semantics.md` |
| `LANG-NO-IMPLICIT-INCLUDE` | The compiler translates a library exactly as written and accepts it when the reference translator does; no added includes. | `language-semantics.md` |
| `LANG-RETRIEVE-PROFILE` | A profile-specific retrieve narrows to resources conforming to that profile, tested at the scan against `meta.profile`. | `language-semantics.md` |

## ARCH — engine/compiler architecture

| id | statement | source |
|----|-----------|--------|
| `ARCH-COMPILER` | Multi-pass compiler over a typed IR, not an interpreter. | `engine-design.md` |
| `ARCH-EXECUTE-NOT-TRANSPILE` | Engine compiles CQL to IR and emits Spark SQL as execution transport over opaque UDFs (not multi-dialect transpiler). | `execution-model.md` |
| `ARCH-SET-EVAL` | Each `ExpressionDef` is evaluated once across the whole subject set, keyed by `__subject_id`. | `engine-design.md` |
| `ARCH-GRAIN` | Every define carries a grain — `Scalar` (one row/subject) or `Relation` (natural resource grain). | `engine-design.md` |
| `ARCH-LIST-NULL-SIDECAR` | A list define's nullity is a narrow per-subject companion `(__subject_id, __is_null)`, read lazily only by observing consumers. | `list-null-tristate.md` |
| `ARCH-RELATION-REDUCE` | Whole-list reductions realized at relation/subject grain, not by forcing an `Array` box. | `list-null-tristate.md` |
| `ARCH-SCAN-SHARE` | Each source retrieve and per-subject context frame are built once and referenced, never re-scanned. | `engine-design.md` |
| `ARCH-HOIST` | Logical CSE: a sub-expression reused across the plan is materialized once as a named column and referenced. | `engine-design.md` |
| `ARCH-SQL-CSE` | Scalar common-subexpression elimination in SQL (hoisting repeated interval bound values or interval towers to `__h<N>` columns). | `engine-design.md` |
| `ARCH-TEMPORAL-CSE` | Precision-normalized temporal towers built off a date operand are projected once and referenced. | `engine-design.md` |
| `ARCH-CTE-DEF` | Define-once/reference-many at relation grain: every define lowers as a named CTE-def/temp view. | `engine-design.md` |
| `DATA-RETRIEVE-VIEW` | A Retrieve lowers to one shared named view `retrieve_<Type>` mounting the whole resource as `(__subject_id, __resource)`. | `engine-design.md` |
| `DATA-RETRIEVE-NO-BARRIER` | A retrieve mount is by DEFAULT a logical non-barrier shared view (independent of `materialize.mode`). | `engine-design.md` |
| `DATA-RETRIEVE-BARRIER-OPT` | OPTIONAL retrieve barriers via `materialize.retrieves = none|costly|all` (default `none`); a bare retrieve never materialized. | `engine-design.md` |
| `DATA-RETRIEVE-SHARE-BARRIER` | A heavily-reused retrieve (consumer count ≥ 4) is promoted to `localCheckpoint` barrier. | `engine-design.md` |
| `ARCH-PROJECT-FUSE` | Fuse a `WithColumns` key into the consuming `Project` (single-use keys only). | `engine-design.md` |
| `ARCH-NO-RUNTIME-SCHEMA` | Lowering derives schemas analytically from the static model; never blocks on a Spark `.schema` round-trip mid plan-build. | `engine-design.md` |
| `ARCH-RESOURCE-HANDLE` | A resource in list/union/Choice position is a demand-set handle (identity + referenced fields); body rehydrated by late per-type join. | `data-model.md` |
| `ARCH-RETRIEVE-PROJECT` | A retrieve's `__resource` is demand-pruned to fields navigated anywhere in the library plus identity/scan backbone. | `data-model.md` |
| `ARCH-PIPELINE` | Pass pipeline: Load → Resolve → Type/Encoding → Normalize → Optimize → Lower → Schedule. | `engine-design.md` |
| `ARCH-SCHEDULE-ACYCLIC` | The Schedule pass follows only intra-library reference edges, the exact set `DefineGraph.assertAcyclic` checks. | `engine-design.md` |
| `ARCH-TYPE-ATTR` | Resolved types are a recomputable synthesized attribute over the `CqlType` lattice. | `engine-design.md` |
| `ARCH-IR` | ADT + transform combinators + fixpoint rule batches; reject GoF Visitor and Rete engines. | `engine-design.md` |
| `ARCH-ENV` | Immutable Environment threads inherited attributes down. | `engine-design.md` |
| `ARCH-PURITY` | Node lowering is pure `(node, Env) => lowered result`; no Spark actions mid-build. | `engine-design.md` |
| `ARCH-LOWERING-NAMING` | Realizers named by target (`ColumnLowering`/`FrameLowering`); operator spanning both named by IR construct (`QueryLowering`). | `engine-design.md` |
| `ARCH-CONDITIONAL` | `if`/`case` lower to UNION ALL of filtered branches; CASE is an optimizer specialization. | `engine-design.md` |
| `ARCH-COND-UNION` | Relation-valued realization of conditional: push condition into each branch's `where` and UNION. | `engine-design.md` |
| `ARCH-PUSHDOWN` | Domain-specific retrieve-pushdown optimizer at the kernel level. | `engine-design.md` |
| `ARCH-ROBUSTNESS` | Robustness pillars: total coverage, small kernel, versioned semantics, verifiers, error model, determinism. | `engine-design.md` |
| `ARCH-HAZARDS` | Design against rule non-termination, Catalyst plan blowup, and pervasive 3VL threading. | `engine-design.md` |
| `ARCH-SEAMS` | Extensibility seams: data model, terminology source, optimization rule, operator, backend (Spark only). | `engine-design.md` |
| `ARCH-DATAMODEL-TRAIT` | Lift FHIR-specific singletons onto a `DataModel` trait; FHIR as implementation #1, serving binding layer and native using statements. | `data-binding.md` |

## EXEC — execution substrate: Spark SQL over opaque UDFs

| id | statement | source |
|----|-----------|--------|
| `EXEC-SQL-BACKEND` | Execution substrate is engine-emitted Spark SQL script (one statement per define) referencing named opaque Scala UDFs. | `execution-model.md` |
| `EXEC-OPAQUE-UDF` | Emit opaque JVM UDF calls at lowering cut-points where relational expansion explodes or semantics are intractable. | `execution-model.md` |
| `EXEC-NATIVE-FIRST` | Lower to native Spark SQL wherever compact and relational; emit UDF only where expansion explodes or semantics intractable. | `execution-model.md` |
| `EXEC-UDF-FIRST` | A repeated, pure-scalar CQL operator lowers to opaque catalog UDF by default (one variant per return type). | `execution-model.md` |
| `EXEC-UDF-COARSE` | A higher-order block combining primitives is admissible only when each primitive is independently offered, it materially beats composites, and is named/detected by pattern. | `execution-model.md` |
| `EXEC-MATERIALIZE-FILTER` | A define materializes iff it is a measure population or its body prunes rows anywhere (retrieve with scan filter or query with where/with/without). | `execution-model.md` |
| `EMIT-SQL-LET-CARRIER` | Thread query `let`s through a chain of `transform`s over a one-element carrier array; where/return/sort read a struct field instead of re-inlined subtree. | `execution-model.md` |
| `EXEC-NATIVE-DISCRIMINATOR` | Choice/handle value exposes discriminator `__choice` and pushdown-relevant scalar fields as native columns. | `execution-model.md` |
| `EXEC-UDF-LIBRARY` | A versioned `runtime` JAR provides opaque UDFs as a fixed, pre-built catalog; registered by name via UC or `spark.addArtifact`. | `execution-model.md` |
| `EXEC-UDF-CATALOG` | Closed catalog of typed UDFs, one variant per return type; compile-time polymorphism, never runtime-dynamic. | `execution-model.md` |
| `EXEC-UDF-REGISTRY-CACHE` | Expensive per-UDF registries (UCUM, timezone data) built once per executor JVM as lazy singletons. | `execution-model.md` |
| `EXEC-CANON-TYPE` | Temporals/quantities/intervals normalized just above retrieve as structs of primitives (not a Spark UDT). | `execution-model.md` |
| `EXEC-DEMAND-TYPING` | Above-retrieve normalization is demand-typed to only fields a measure computes on. | `execution-model.md` |
| `EXEC-PLAN-VISIBILITY` | SQL script is text — reviewable, auditable, no opaque proto and no depth cap. | `execution-model.md` |
| `EXEC-PATIENT-PARTITION` | Partition population plan by `__subject_id` so patient's resources colocate and per-context joins run within-partition. | `execution-model.md` |
| `EXEC-CQF-LEVERAGE` | CQF leveraged three ways — ELM front end, ground-truth oracle, evaluation lessons (parse-once types, per-JVM caches). | `execution-model.md` |
| `EXEC-SELFDIFF` | Opaque-UDF vs native lowering and normalized-type vs ISO-string lowering must produce identical results; divergence is a bug. | `execution-model.md` |
| `EXEC-UC-UDF-BACKEND` | UC-governed `CREATE FUNCTION` over the same `runtime` catalog is a deployment backend alongside OSS `spark.udf.register`. | `uc-scala-udf-port.md` |
| `EXEC-UC-STRUCT-ARG` | UC catalog handler binds a SQL `STRUCT` via Scala case class whose schema exactly matches, not `Row`. | `uc-scala-udf-port.md` |
| `EXEC-UC-MANIFEST` | Engine publishes platform-neutral function manifest; downstream host generates UC DDL and uploads fat jar. | `uc-scala-udf-port.md` |
| `EXEC-UC-NAME-RESOLVE` | Emitted SQL keeps bare `cql_*` names; host places functions on SQL search path. Emit stays host-neutral. | `uc-scala-udf-port.md` |

## EVAL — SQL-over-opaque-UDF execution layers

| id | statement | source |
|----|-----------|--------|
| `EVAL-TWO-LAYER` | Every measure lowers into a data-access layer and a calc layer (scalar CQL semantics via catalog operators), woven into one plan. | `execution-model.md` |
| `EVAL-OP-GRANULARITY` | Calc layer is a fixed, versioned catalog of ELM-mirroring operators; fine-grained composition is the proven default. | `execution-model.md` |
| `EVAL-NATIVE-MARSHAL` | Operators take/return native Spark types; box only the genuinely-dynamic live arm of a sum type. | `execution-model.md` |
| `EVAL-3VL-VIA-SQL` | Logical, nullological, and primitive equality/comparison lower to native Spark expressions. | `execution-model.md` |
| `EVAL-OP-CATALOG` | Calc set is ~25–30 fixed-output operators, version-pinned to compiler and shipped via `addArtifact`. | `execution-model.md` |
| `EVAL-COMPOSITE-IN-PLAN` | Product/list composites assembled by native Spark constructors; catalog UDF never returns bespoke composite. | `execution-model.md` |
| `EVAL-OPAQUE-VALUE-OPS` | Catalog UDF may accept bespoke composite as opaque `Row` for whole-value ops; bespoke field access always plan op or inlined. | `execution-model.md` |
| `EVAL-CHOICE-NARROW` | Choice navigation with statically-known target type lowers to monomorphic `cql_choice_nav_<T>`. | `execution-model.md` |
| `EVAL-UNION-DEFER` | Heterogeneous `union` pushes post-union operation into each arm and unions the homogeneous result. | `execution-model.md` |
| `EVAL-CHOICE-ENVELOPE` | Polymorphic carry uses uniform `struct<__type, __body: binary>` envelope, not per-arm-widened struct. | `execution-model.md` |
| `EVAL-HOF-CONTROL` | Calc-forcing control flow lowers to Spark control-flow/HOF expressions whose leaves are native/catalog UDFs. | `execution-model.md` |
| `EVAL-RETRIEVE-RELATIONAL` | A `Retrieve` lowers to compiler-emitted relational subplan, never a runtime UDTF. | `execution-model.md` |
| `EVAL-VS-PUSHDOWN` | Value-set membership is retrieve-level broadcast semi-join by default. | `execution-model.md` |
| `EVAL-JOIN-PUSHDOWN` | `with`/`without` lower to SEMI/ANTI joins with 3VL `such that` on the `ON` clause. | `execution-model.md` |
| `EVAL-STATUS-RELATIONAL` | QICore status/profile helpers inline to relational `status`-set predicates and profile filters. | `execution-model.md` |
| `EVAL-GLOBAL-PRELOAD` | Global state (value-sets, code systems, parameters) loaded once per executor JVM, version-pinned. | `execution-model.md` |
| `EVAL-OPS-CORE` | CQL semantics live once in `…runtime.eval.CqlOps` (~25–30 operator fns) + the tree-walker. | `execution-model.md` |

## DATA — physical data model, encoding, I/O

| id | statement | source |
|----|-----------|--------|
| `DATA-RETRIEVER` | Data access is an adapter returning model-conformant, context-keyed frames. | `data-format.md` |
| `DATA-CONTEXT-RESOURCE` | Subject-grouped context frame carries context resource as struct column. | `data-model.md` |
| `DATA-TERMINOLOGY-ADAPTER` | Terminology enters through TerminologyProvider adapter as broadcastable, version-pinned tables. | `data-format.md` |
| `DATA-VALUE-TAXONOMY` | Every lowered value lives in one physical representation; planner inserts conversions at boundaries. | `data-format.md` |
| `DATA-LIST-ENCODING` | Default `Relation` encoding; `Array` forced at specific boundaries; `__ordinal` mandatory end to end. | `data-format.md` |
| `DATA-SUBJECT-KEY` | `__subject_id` is normalized top-level key attached at `DataProvider` boundary, not derived mid-hot-path. Amended 2026-10-21: a resource is patient-context when it has a `subject`, `patient` or `beneficiary` reference (`FhirModel.SubjectReferenceFields`), the same fields the client's reshape reads. | `data-format.md` |
| `DATA-VALUE-CARRIER` | Define's output column is `__val` (scalar for primitive, struct for tuple/resource, array only when boxed). | `data-format.md` |
| `EMIT-SQL-VALUE-COLUMN` | Every define result carries payload in `__val`; scalar is `(__subject_id, __val)`, relation is `(__subject_id, __ordinal, __val)`. | `data-format.md` |
| `EMIT-SQL-OBJECT-PREFIX` | Emitted session object names may be namespaced by caller-supplied `objectPrefix` (prefixes materialized reads that are content-addressed). | `data-format.md` |
| `EMIT-SQL-DROP-REL-PREFIX` | CQL define names are unique within a library so scalar and relation defines never collide when sanitized to the same object name. | `data-format.md` |
| `DATA-SYSTEM-COLUMNS` | Reserve the `__*` namespace; Retriever rejects source columns matching it. | `data-format.md` |
| `DATA-DEFINE-DF` | Every `ExpressionDef` produces a result whose schema is unmistakably its CQL type. | `data-format.md` |
| `DATA-MATERIALIZE` | Selective materialization in production; full materialization as debug/audit mode; all configurable. | `data-format.md` |
| `DATA-SKIP-UNFILTERED` | Skip materializing bare unfiltered-retrieve pass-through defines (default on). | `data-format.md` |
| `DATA-MATERIALIZE-POLICY` | Materialization is a single cost-driven policy `materialize.mode = all|costly|none` (default `costly`). | `data-format.md` |
| `DATA-EXPORT` | Engine outputs results in CQL-declared data model + `__resource_*` lineage; report compilation is downstream. | `data-format.md` |
| `DATA-SCHEMA-CONTRACT` | Lowering reads schema from `Encoding(typeOf(node))`, never a live frame. | `data-format.md` |
| `DATA-MODEL-SEAM` | External data models resolve through one generic seam; FHIR R4 is first implementation. | `data-model.md` |
| `DATA-MODEL-STATIC-STRUCT` | Demand-hot fields extracted to typed columns; whole-body/het-Choice payload rides flat-STRUCT carrier. | `data-model.md` |
| `DATA-MODEL-SCHEMA` | Model→Spark encoding: primitives → scalars; `0..*` → array; `value[x]` → one typed slot per type; extensions → closed allow-list. | `data-model.md` |
| `DATA-NO-EMPTY-STRUCT` | A complex type truncating to zero fields at nesting cap is omitted, not emitted as field-less `STRUCT<>`. | `data-model.md` |

## TYPE — FHIR result typing & static schema

| id | statement | source |
|----|-----------|--------|
| `TYPE-FHIR-MODEL` | FHIR result typing derives from static FHIR R4 element model; `typeOf` is precise at leaves, no `AnyType`. | `data-model.md` |
| `TYPE-OFFLINE-COMPILE` | Typing + lowering perform zero Spark round-trips; heterogeneous `Choice` members reconstructed from static model. | `data-model.md` |
| `TYPE-VARIANT-SCHEMA-ON-READ` | `FhirSchema.rawResourceSchema` is extraction/typing oracle — schema consulted to derive DDL a `variant_get` rehydrates to. | `data-model.md` |

## BUILD-FHIR — build-time code generation

| id | statement | source |
|----|-----------|--------|
| `BUILD-FHIR-CODEGEN` | FHIR model code-generated at build time from vendored FHIR R4 XSDs (deterministic + incremental). | `data-model.md` |

## TECH — technology choices

| id | statement | source |
|----|-----------|--------|
| `TECH-SCALA` | Scala for engine core (Java fallback; Python only as user API). | `technical-implementation.md` |
| `TECH-TOOLCHAIN` | Toolchain pinned to DBR 18 floor: Spark 4.1.2, Scala 2.13.16, JDK 17, Gradle. | `technical-implementation.md` |
| `TECH-ELM-INPUT` | CQL is public API; translates CQL→ELM in-process. ELM is internal seam, not contract. | `technical-implementation.md` |
| `TECH-REUSE` | Reuse CQL→ELM front end + tests; reimplement runtime/operators; integrate (don't build) expansion. | `technical-implementation.md` |
| `TECH-SEMANTIC-LIB` | Standalone, versioned semantic library from native Spark expressions; UDFs a costed escape hatch. | `technical-implementation.md` |
| `TECH-LOWERING-TARGET` | Lower to Spark SQL script (text) emitted by engine, run via `spark.sql`. | `technical-implementation.md` |
| `TECH-SPARK-MAP` | Defined mapping of engine needs to Spark mechanisms. | `technical-implementation.md` |
| `TECH-DETERMINISM` | No reliance on hashmap order, wall clock, or unstable shuffles; record engine/version/config with output. | `technical-implementation.md` |
| `TECH-CONFIG` | Configuration is an immutable object supplied at environment level, frozen into `Env`. | `technical-implementation.md` |
| `DEBUG-PLAN-DUMP` | Debug-only switch (`-Dcqldatabricks.dumpDir`) dumps lowered library's optimized artifacts per fixture. | `technical-implementation.md` |
| `DEBUG-SQL-BARRIER` | Dump makes materialization barriers visible in assembled SQL and appends each barrier's defining subplan. | `technical-implementation.md` |

## CONF — conformance & testing

| id | statement | source |
|----|-----------|--------|
| `CONF-GATES` | Four required CI gates, all green to ship. | `testing-and-conformance.md` |
| `CONF-GATE-OPERATOR` | Gate 1: per-operator conformance against `cqframework` suite + hand cases. | `testing-and-conformance.md` |
| `CONF-GATE-PROPERTY` | Gate 2: property-based differential over nullable/interval/quantity/choice vs Java CQF engine. | `testing-and-conformance.md` |
| `CONF-GATE-MEASURE` | Gate 3: whole-measure differential against Java CQF engine, patient-by-patient. Amended 2026-10-20: the corpus measures are written for this repository, and the measurement period is passed to both engines as a run-time parameter, never patched into the CQL. | `testing-and-conformance.md` |
| `CONF-MEASURE-LINK` | Multi-library eCQM flattened into one self-contained library before lowering. | `testing-and-conformance.md` |
| `CONF-GATE-SCOREKEY` | Gate 4: whole-measure scorekey diff — engine per-patient population membership reduced to MeasureReport count must equal answer key. Amended 2026-10-20: the answer key is hand-written per patient for the repository's own measures, not a published MeasureReport. | `testing-and-conformance.md` |
| `CONF-GOLDEN-PLAN` | Golden snapshots surface optimizer/normalization changes as code-review diffs. | `testing-and-conformance.md` |
| `CONF-REF-DEVIATION` | A divergence from Java CQF reference admissible only as DECLARED, JUSTIFIED deviation on the fixture. | `testing-and-conformance.md` |
| `CONF-GATE-EMIT-INVARIANT` | Server-free, data-free type invariants on emitted SQL of every measure. | `testing-and-conformance.md` |
| `CONF-PERF-TIME` | Perf gate on CPU-vs-wait timing model. | `testing-and-conformance.md` |
| `CONF-PERF-BARRIER-DETAIL` | Extends perf-time to barrier granularity. | `testing-and-conformance.md` |
| `CONF-GROUND-TRUTH` | Reference in-memory providers live in `:compiler:cqf`, the conformance oracle. | `testing-and-conformance.md` |
| `CONF-TESTDATA` | Test data: synthetic FHIR + measure decks + targeted edge fixtures. Amended 2026-10-20: the steward decks were removed; measure decks are written for this repository. | `testing-and-conformance.md` |
| `CONF-VENDOR-ECQM` | Measure CQL vendored from pinned official eCQI package via `compiler/conformance/ecqm/vendor-ecqm.py`. Amended 2026-10-20: no official package is vendored. `vendor-ecqm.py` is a user-run loader that writes unmodified packages outside the tracked tree. SUPERSEDED 2026-10-21 by `CONTENT-ECQI-USER-PACKAGE`: `vendor-ecqm.py` is removed; the one eCQI loader is the importer's, and the conformance corpus holds only measures written for this repository. | `testing-and-conformance.md` |
| `CONF-REF-ANSWER-KEY` | Spark-free gate scoring Java CQF reference against shipped MeasureReport answer keys. Amended 2026-10-21: the answer key is each measure's hand-written `answer-key.json` (per patient, per population), the same key `CONF-GATE-SCOREKEY` reads; a mismatch fails rather than cancels. | `testing-and-conformance.md` |
| `CONF-MEASURE-DISCOVERY` | The measure gates discover the corpus measures; none is listed in code. A measure is a directory `data/measures/<library>/` holding one `<Type>.ndjson` per resource type and an `answer-key.json` that names the library, its version, its measurement period and its populations (population define to population code). The include closure comes from the translated ELM. Adding or removing a measure changes no spec. Added 2026-10-21. | `testing-and-conformance.md` |
| `CONF-REF-PROFILE` | Reference retrieve provider honors a retrieve's PROFILE against `meta.profile`. | `testing-and-conformance.md` |
| `CONF-VERIFIER` | Verifier passes assert post-conditions between compiler stages in test/debug builds. | `testing-and-conformance.md` |
| `CONF-TEST-CONVENTIONS` | Conventions for writing tests. | `testing-and-conformance.md` |
| `CONF-CERTIFICATION` | Certification alignment with external digital quality measure certification programs. Amended 2026-10-20: on hold; certification runs official measures, which this repository no longer ships. | `testing-and-conformance.md` |
| `CONF-TEST-LAYERS` | Tests tiered by Spark dependency: L1 semantic / L2 plan-shape / L3 result-equivalence / L4 scale. | `testing-and-conformance.md` |
| `CONF-SPARK-CONNECT` | Tests and production drive Spark through Spark Connect client. | `testing-and-conformance.md` |
| `CONF-SCALE-REMOTE` | Same Connect client targets always-on/serverless Databricks endpoint for warm loop and L4 scale. | `testing-and-conformance.md` |
| `CONF-TEST-ARTIFACTS` | Per-operator test cases capture inspectable artifacts under `build/test-artifacts/`. | `testing-and-conformance.md` |

## PROC — implementation-process requirements

| id | statement | source |
|----|-----------|--------|
| `PROC-DESCRIPTIVE-ID` | Every referenceable label is a descriptive mnemonic, never a bare sequence. Sequence goes in explicit `after` column. | `../README.md` |
| `PROC-CITE-REQ` | Every change cites the requirement id(s) it realizes in code, commits, PRs, and tests. | `overview.md` |
| `PROC-TDD` | Each new kernel primitive ships with operator-level + desugaring/lowering tests before done. | `roadmap.md` |
| `PROC-DRY` | Every piece of knowledge has one authoritative representation; callers reference it, never restate. | `engineering-principles.md` |
| `PROC-SOLID` | Single-responsibility modules, extension via seams not edits, substitutable providers, minimal interfaces. | `engineering-principles.md` |
| `PROC-DATA-PROVENANCE` | A data element reachable from a measure boundary but not used by any measure define (dead code) is not part of the measure. Reachability walks the compiler's describe graph. | `execute-model.md` |

## BIND — data binding / adapter layer

| id | statement | source |
|----|-----------|--------|
| `BIND-PROVIDER` | SQL back end lowers each retrieve to call a client-supplied SQL UDTF. | `data-binding.md` |
| `BIND-CONTRACT` | Every binding yields fixed model-derived mount schema `(__subject_id, __resource)`. | `data-binding.md` |
| `BIND-RESHAPE` | One implementation turns FHIR data into the mount contract: generated SQL over a named relation (`cql_databricks.compiler.reshape`), used by the session mount, the publish step and the loader. JSON text is parsed against the model's struct; a structured relation is rebuilt field by field, a missing field a typed null, an extra one dropped, a safe widening cast. `__subject_id` is `id` for Patient, the trailing id of `subject`, `patient` or `beneficiary` `.reference` for a Patient-compartment type, else null. No SQL text is taken from a caller. | `data-binding.md` |
| `BIND-SCHEMA-CHECK` | A source is checked against the model before it is mounted or published, naming every problem at once: a missing field WARN, a safe widening or a dropped field INFO, and as ERROR a type no cast fixes safely, a merged choice field (`value` for `valueQuantity`…), a reference or other struct stored as a JSON string, no `id`, or no way to derive `__subject_id`. A source with an ERROR is refused. Checking the fields one query demands is not built. | `data-binding.md` |
| `BIND-PUBLISH` | The client publishes a data source over tables (`publish_data_source`), as the caller, writing its declaration and never data: `CREATE SCHEMA IF NOT EXISTS`, one `retrieve_<Type>` per type with the contract signature (`BIND-EXPLICIT-PARAMS`), the model's return struct and the reshape of its table as the body (`BIND-RESHAPE`), then the `cql_databricks_data_source` tag and an optional description (`DATA-SOURCE-DESCRIPTION`). It returns a plan with the SQL and every check finding; `run()` refuses on any ERROR (`BIND-SCHEMA-CHECK`), then re-checks the layout (`BIND-DATA-SOURCE-LAYOUT`). Sources are `catalog.schema.table` names only; a DataFrame or `FhirJson` is refused, naming the fix. The functions ignore the pushdown arguments. | `data-binding.md` |
| `BIND-MANIFEST` | Binding is data passed over compile API: per-type map `fhirType` → `{ function, catalog?, … }`. | `data-binding.md` |
| `BIND-DATA-SOURCE-LAYOUT` | Compiler owns data-source schema layout: at least one `retrieve_<Type>` UDTF, optional `change_data_log(data_type, updated_at)`. | `data-binding.md` |
| `BIND-RETRIEVE-UDF` | Each retrieve binds to SQL UDTF `fn(<eight explicit parameters>) RETURNS TABLE(__subject_id, __resource)`. | `data-binding.md` Amended 2026-10-11 by `EMIT-ROOT-INDEPENDENT`: seven parameters (`BIND-EXPLICIT-PARAMS`). A UDTF's rows depend only on its arguments and its data source's data. |
| `BIND-UC-PRECREATE` | On Databricks, retrieve UDTFs are precreated UC SQL functions; engine resolves by name. | `data-binding.md` |
| `BIND-PUSHDOWN` | Engine passes statically-known predicates + context to UDTF as literal arguments. | `data-binding.md` |
| `BIND-EXPLICIT-PARAMS` | UDTF signature is eight explicit, named, typed, positional parameters with defaults: library, value_set, code_property, codes, date_property, date_low, date_high, parameters. | `data-binding.md` Amended 2026-10-11 by `EMIT-ROOT-INDEPENDENT`: seven parameters; `library` is removed, so no argument names a library. `parameters` carries only the parameters the current define's closure uses, keyed by its own library's names; the context base gets `map()`. |
| `BIND-CAPABILITY` | Pushdown is optimization not correctness; UDTF honors as many arguments as it can and engine re-applies filters. | `data-binding.md` |
| `BIND-TERMINOLOGY` | Value sets enter via TerminologyProvider.expand as pre-expanded, version-pinned tables. | `data-binding.md` |
| `BIND-TERMINOLOGY-TABLE` | Terminology source is schema tagged `cql_databricks_terminology` holding `valueset(valueset_id, system, code, version)`. | `data-binding.md` |
| `BIND-ESCAPE` | Hand-written JVM `DataProvider` remains a supported power-user escape hatch. | `data-binding.md` |
| `BIND-REUSE` | Write shaping logic once as inlinable SQL function; UDTF body calls it per source arm. | `data-binding.md` |
| `BIND-CANON-CONTRACT` | Maturity ladder (L1 run-at-all, L2 structure & time, L3 scale & trust) of properties a customer's model must exhibit. | `data-binding.md` |
| `BIND-CAPABILITY-OVERLAP` | Customer declares one thing per resource type — a typed subset of FHIR model. Engine intersects declared with demand, extracts overlap as typed columns. | `data-binding.md` |
| `BIND-DESCRIBE-RETRIEVES` | Compiler computes scan contract: per scan the owning define, resource type, profile constraint, code property, filter, date filter. | `data-binding.md` |
| `BIND-CONCEPTMAP` | Local→standard terminology translation via FHIR `ConceptMap` table. | `data-binding.md` |
| `BIND-CONCEPTMAP-PREIMAGE` | Predicate pushdown through ConceptMap: join value-set's standard codes to map's target, take source codes as scan filter. | `data-binding.md` |
| `BIND-SELFDIFF` | Mapping checked like pushdown: self-diff a mapping and require empty result. | `data-binding.md` |
| `BIND-STD-SCOPE` | Standards-reuse scope: ADOPT `ConceptMap` and `FHIRPath`; DEFER `StructureMap` and SQL-on-FHIR `ViewDefinition`. | `data-binding.md` |

## PIPE — pipeline-plan compile surface

| id | statement | source |
|----|-----------|--------|
| `PIPE-SQL-DAG-REUSE` | `/compile-pipeline` lowers the whole measure once then cuts DAG at each materialized define into one dataset recipe. | `pipeline-runtime.md` Amended 2026-10-11 by `EMIT-ROOT-INDEPENDENT`: a durable plan cuts at every define (`materialize.mode=all`) and never at a shared retrieve read (`materialize.retrieves=none`), whose selection counts call sites across the closure. |
| `PIPE-DURABLE-IDENTITY` | Opt-in durable plans expose versioned compiler-owned logic and shape fingerprints. | `pipeline-runtime.md` Amended 2026-10-11 by `RUN-CLIENT-RESULT-IDENTITY`: each recipe carries one `fingerprint` (format `cql-result-identity-v1`) over its query, its parameter values, its upstream fingerprints and the bound data source's address; the logic and shape hashes are removed. A durable compile that retrieves data without `dataSource` fails. |
| `PIPE-EVAL-SCOPED-READ` | Pipeline compile request may name evaluation set; each recipe reads published sibling scoped to that evaluation. | `pipeline-runtime.md` |
| `PIPE-READS-SORTED` | A recipe's `reads` (published siblings it reads) is emitted sorted, not in walk order. | `pipeline-runtime.md` |
| `PIPE-MULTI-LIB` | `/compile-pipeline` accepts `libraries` manifest (array of `{elm, deps}`); produces single merged PipelinePlan. | `pipeline-runtime.md` |
| `PIPE-MULTI-DESCRIBE` | `/describe` accepts same `libraries` manifest and returns merged LibraryGraph. | `pipeline-runtime.md` |
| `PIPE-EMIT-SQL` | `/compile-pipeline` emits ready-to-deploy SQL file per recipe (`CREATE OR REFRESH MATERIALIZED/VIEW`). | `pipeline-runtime.md` |

## EMIT — emit + interchange surface

| id | statement | source |
|----|-----------|--------|
| `EMIT-DUAL-BACKEND` | Lowering produces one shared dataset graph; one execution path and reference pipeline is deployment wrapper. | `pipeline-runtime.md` |
| `MATERIALIZE-SINK` | Cost model decides what to materialize; pluggable sink seam decides where. | `pipeline-runtime.md` |
| `EMIT-SQL-EXECUTE` | The SQL run path: execute each compiled statement via `spark.sql` in dependency order. | `pipeline-runtime.md` |
| `EMIT-DATAFRAME` | Optional engine-free export (native Python DataFrame code) for consumers running without compiler. | `pipeline-runtime.md` |
| `EMIT-API` | Light programmatic library + CLI (`transpile`/`emit`/`apply`/`dryRun`). | `pipeline-runtime.md` |
| `EMIT-INTERCHANGE` | What crosses a boundary is CQL+ELM package, never compiled artifact (bound to sender's catalog). | `pipeline-runtime.md` |
| `EMIT-INSPECT` | Primary inspection is `cql(...).explain()` / `.show()` + read-only plan dump. | `pipeline-runtime.md` |
| `EMIT-DATA-CONTRACT` | Emit target binds to FHIR-in-Delta layout + valueset-as-table convention. | `pipeline-runtime.md` |
| `EMIT-SQL-TEMPORAL-UDF-OPAQUE` | Each repeated CQL temporal operator lowers to one opaque JVM catalog-UDF call. | `execution-model.md` |
| `EMIT-SQL-FLAT-CHOICE` | Heterogeneous-union alias's discriminated struct flattened to top-level demand-pruned slots. | `execution-model.md` |
| `EMIT-SQL-QUANTITY-UDF` | ELM `ConvertQuantity` with runtime source unit lowers to `cql_convert_quantity` UDF. | `execution-model.md` |
| `EMIT-SQL-MEMBERSHIP-EQUIJOIN` | Value-set membership on retrieve-scan path lowered as EQUI-join. | `data-binding.md` |
| `EMIT-UDF-QUALIFY` | Qualify every emitted `cql_*` UDF call as `<schema>.cql_fn(…)` when needed (not done by default; opt-in per client). | `data-binding.md` |
| `EMIT-DESCRIBE-ENCODING` | `describe` publishes het-Choice carrier ENCODING and per-resource DEMAND. | `execution-model.md` |
| `EMIT-DESCRIBE-RETRIEVE-TYPES` | `describe` lists every resource type the library retrieves, sorted, including a retrieve that navigates no field and the context's own retrieve; empty for data-free CQL. A caller binds exactly the data a run needs (`CLIENT-EXEC-DISCOVERY`). | `compiler-service.md` |
| `EMIT-ROOT-INDEPENDENT` | A define's emitted SQL, its recipe name and whether it is materialized are the same whichever library is the root, so a library that includes another reuses the included library's stored results (`DEC-LIBRARY-OWNS-RESULTS`, `RUN-CLIENT-RESULT-IDENTITY`). No root library name in the SQL: measure selection moves from the retrieve's `library` argument into the data-source binding. Names come from the owning library and version, never the root. Materialization cut points belong to the define, not the closure. Built: the pipeline compile names the root's own defines by their library as the linker names an included library's (`LibraryLinker.ownerQualified`), so every object and alias is `<library>_<define>`, and names each recipe `<owner>_<owner version>__<define>` with that owner as its origin. The retrieve `library` argument is removed (`BIND-EXPLICIT-PARAMS`): a population is a data source (`DATA-CONFORMANCE-MEASURE-SELECT`). Parameter claims and the retrieve `parameters` map use each define's own library's parameter names (`ParameterScope`; HL7 CQL Library > Parameters: an included library's parameter takes the root's value when the root declares the same name, else its own default). Describe and the session compile keep the authored names they publish. Checked by `IncludedReuseSpec`. | `pipeline-runtime.md` |
| `EMIT-SQL-RETRIEVE-DEMAND-BOUND` | Retrieve's scanned resource bounded by DEMAND, not resource width. | `execution-model.md` |

## BUILD — CQL Builder: modules, client, compile service

| id | statement | source |
|----|-----------|--------|
| `BUILD-TAXONOMY` | Feature → component → module. Feature is deployable, functional unit with own docs; component is build unit; no product level. Dependencies one way, HTTP counts. Four features: loader, compiler, agent, designer. | `structure.md` |
| `BUILD-SYSTEMS` | Per-feature: owns, does not own, interface. One-way order held; HTTP counts. Common library `cql_databricks_common` is own distribution at `common/`. | `systems.md` |
| `BUILD-CLIENT-NAMESPACE` | `cql-databricks` ships `cql_databricks.compiler` and `cql_databricks.data_bindings`. | `systems.md` |
| `BUILD-CLIENT-SCOPES` | The client holds `compile`, `run` and `materialize` (`cql_databricks.result`); its mode is fixed when constructed. The data a compile binds to is chosen at each compile (`CLIENT-EXEC-DISCOVERY`), so a client built before a mount reads it. Amended 2026-10-11. | `systems.md` |
| `BUILD-MODULES` | Gradle projects: `compiler/{core,service,server,runtime}`, conformance, `common`, cql-databricks Python. | `modules.md` |
| `BUILD-COMPILE-SERVICE` | Compiler-service logic + OSS host live in-repo; `/compile-sql`, `/compile-pipeline`, `/describe`. | `pipeline-runtime.md` |
| `BUILD-COMPILE-LINK` | Compile endpoints accept root ELM + dependency array; flatten via `LibraryLinker`. | `pipeline-runtime.md` Amended 2026-10-11 by `EMIT-ROOT-INDEPENDENT`: the linked library records each merged name's owning library and version (`Library.owners`). |
| `BUILD-CLIENT` | Python `cql-databricks` exposes `cql(library, target, boundaries)` + `load_library` + provider. | `pipeline-runtime.md` |
| `BUILD-RESOLVE` | Client resolves every setting by one precedence: explicit arg → spark.cqldatabricks.* → CQL_DATABRICKS_* env → compiled-in default → fail. No setting is discovered: the data a run reads is chosen at compile time by the library's binder (`CLIENT-EXEC-DISCOVERY`). Amended 2026-10-11: the UC role-tag (`cqldatabricks.role`) and default-name (`fhir`, `fhir.valueset_expansion`) tiers are removed. Amended 2026-10-11: serverless refuses to set a custom Spark conf key (`CONFIG_NOT_AVAILABLE`), so notebooks and Jobs pass settings as `connect()` keywords or env. A new setting gets a keyword and an env var, never a `spark.cqldatabricks.*` key. | `pipeline-runtime.md` |
| `BUILD-ENV` | Client session is CQL builder; each binding dual-represented. | `pipeline-runtime.md` |
| `BUILD-TYPE-METADATA` | Define's declared result type is authoritative metadata, distinct from physical columns. | `pipeline-runtime.md` |
| `BUILD-VERSION-PIN` | Deployed pipeline compiles against pinned compiler + terminology version. | `pipeline-runtime.md` |
| `BUILD-CLIENT-SESSION` | Session holds endpoint/auth/library-source/fhir-catalog/context/data-model resolved through BUILD-RESOLVE. | `pipeline-runtime.md` |
| `BUILD-CLIENT-SESSION-PARAMETERS` | Client session may set parameter values once, for every query; service applies only those the library declares. | `pipeline-runtime.md` |
| `BUILD-CLIENT-QUERY` | `cql(source)` returns lazy `CqlQuery` handle; `source` is raw CQL/ELM/resource path or bound `Library`. | `pipeline-runtime.md` |
| `BUILD-CLIENT-POPULATIONS` | `exec()` with no targets defaults to measure population defines. | `pipeline-runtime.md` |
| `BUILD-LIBRARY-PROVIDER` | Library source resolution is client-side seam; OSS ships `FileLibraryProvider` over search path. | `pipeline-runtime.md` |
| `BUILD-RESOLVE-COMPILE` | One resolution for every caller: `cql_databricks.compiler.resolve`. | `compiler-service.md` |
| `BUILD-CLIENT-COMPAT` | Client↔service wire bidirectionally tolerant; Python surface append-only. | `client-compatibility.md` |
| `BUILD-META` | Client returns first-class metadata: `describe(lib) → LibraryGraph`. | `pipeline-runtime.md` |
| `BUILD-URL-KEBAB` | Every HTTP endpoint path lowercase kebab-case (no camelCase or underscores in path segments). | `pipeline-runtime.md` |

## Retired / superseded requirements

Ids kept so existing code/commit citations still resolve, but NOT live — do not build against these.

| id | status | source |
|----|--------|--------|
| `EXEC-SQL-DECIMAL-STEP` | Known gap (tracked in `OperatorTests.KnownDivergences`). The SQL lowering of `Predecessor`/`Successor`/`HighBoundary` over `Decimal` does not yet apply CQL's 10^-8 decimal precision step. | `execution-model.md` |
| `CONF-PERF-BUDGET` | Dormant — baselines removed, gate code + bless path kept. | `testing-and-conformance.md` |
| `ARCH-TEMPORAL-COMPANION` | Rejected — precomputed timestamp companion for retrieved temporal fields; input leg's recompute already amortized by CSE + barriers. | `engine-design.md` |
| `EVAL-DEFINE-EVALUATOR` | Fallback-only — per-define opaque ELM tree-walker; not baseline. | `execution-model.md` |
| `EVAL-RETURN-SHAPE` | Rejected — explicit-runtime-schema return via `udf(fn, dataType)` not supported. | `execution-model.md` |
| `DEBUG-SQL-EMIT` | Removed — read-only SQL decompiler superseded by plan-dump relation-tree render. | `execution-model.md` |
| `EMIT-SQL-TEMPORAL-UDF` | Dormant — readability fold of SQL temporal towers. Superseded by `EMIT-SQL-TEMPORAL-UDF-OPAQUE`. | `execution-model.md` |
| `EMIT-SQL-VARIANT-CHOICE` | Retired — het-Choice VARIANT encoding; flat-native carrier already solves width/O(N²) problem via `ARCH-RESOURCE-HANDLE` + `EMIT-SQL-FLAT-CHOICE`. | `execution-model.md` |
| `EMIT-SQL-BARRIER-TABLE` | Reverted — briefly made emit barrier a managed `CREATE TABLE`; reverted to `CACHE TABLE`. | `data-format.md` |
| `EMIT-NOTEBOOK` | Retired — pretty-printed DataFrame-code notebook; moot when dataset body is `cql()` stub. | `pipeline-runtime.md` |
| `EMIT-CONFIG-HOIST` | Retired — hoisting repeated measurement-period literal out of DataFrame code; moot under runtime-compile. | `pipeline-runtime.md` |
| `BUILD-COMPILE-SPARK-URL` | Retired — compile service no longer requires reachable Spark (choice member schemas from static model). | `pipeline-runtime.md` |
| `PIPE-REFERENCE-RUNTIME` | Retired — Enzyme gets no incrementality through opaque UDF catalog; full-refresh orchestration does identical work without planner. | `pipeline-runtime.md` |
| `PIPE-RUNTIME-COMPILE` | Survives as documented principle; SDP runtime retired. `cql()` runtime-compiles to Spark SQL. | `pipeline-runtime.md` |
| `PIPE-SDP` | Retired — reference deploy artifact was OSS Spark Declarative Pipeline. | `pipeline-runtime.md` |
| `PIPE-PACKAGE` | Retired with SDP runtime. | `pipeline-runtime.md` |
| `PIPE-APPLY` | Retired with SDP runtime. | `pipeline-runtime.md` |
| `BIND-HINT-ENVELOPE` | Retired — `fn(context VARIANT, hints VARIANT)` envelope pair. Replaced by `BIND-EXPLICIT-PARAMS`. | `data-binding.md` |
| `BIND-FEDERATE` | Retired — federation inside client SQL UDTF body, folded by literal arguments. | `data-binding.md` |
| `BIND-SOURCE-CATALOG` | Retired — no `TBLPROPERTIES` catalog interrogation; binding table passed over compile API. | `data-binding.md` |
| `BIND-SOURCE-PRUNE` | Retired — source pruning is Catalyst arm-elimination inside UDTF. | `data-binding.md` |
| `BIND-MAP` | Retired — most-specific-source selection subsumed by ordinary SQL in UDTF body. | `data-binding.md` |
| `BIND-SOURCE-DISCRIMINATOR` | Retired — engine-enforced cohort filter. Conformance defines cohort by isolated fixture data. | `data-binding.md` |
| `BIND-RETRIEVE-SUBJECT` | Retired 2026-10-11 — the optional `retrieve_<Type>_ids(subject_ids, <the eight>)` variant and the subject-scoped compile that called it. No caller scoped a compile to patients. Earlier deploys' `_ids` functions are dropped by provisioning. | `data-binding.md` |
| `CONF-GATE-SELFDIFF` | Retired as dedicated gate; now enforced transitively by `CONF-GATE-MEASURE`. | `testing-and-conformance.md` |
| `EMIT-SQL-CHOICE-INTERVAL` | Evaluated and rejected — folding choice dispatch into coarse UDF increased emitted SQL even as CASE counts fell. | `execution-model.md` |
| `EVAL-EVAL-TREE-SHARED` | Applies only to fallback `EVAL-DEFINE-EVALUATOR`; the ELM tree built once per executor JVM. | `execution-model.md` |
| `EXEC-SQL-TEMPORAL-RUNTIME-UNIT` | Known gap on native `make_interval` path (runtime unit dispatch costs 14–17x plan size). Honored on opaque-UDF path. | `execution-model.md` |
| `PIPE-READ-DATASET` | A materialized retrieve read published as its own `DatasetRecipe`; name CONTENT-ADDRESSED, `read_<type>[_<filter>]_<hash8>`. | `pipeline-runtime.md` |
| `PHASE-0` | Old roadmap phase. Retired: old roadmap phase. | `roadmap.md` |
| `PHASE-1` | Old roadmap phase. Retired: old roadmap phase. | `roadmap.md` |
| `PHASE-2` | Old roadmap phase. Retired: old roadmap phase. | `roadmap.md` |
| `PHASE-3` | Old roadmap phase. Retired: old roadmap phase. | `roadmap.md` |
| `PHASE-4` | Old roadmap phase. Retired: old roadmap phase. | `roadmap.md` |
| `PHASE-5` | Old roadmap phase. Retired: old roadmap phase. | `roadmap.md` |
| `PHASE-6` | Old roadmap phase. Retired: old roadmap phase. | `roadmap.md` |
| `PHASE-7` | Old roadmap phase. Retired: old roadmap phase. | `roadmap.md` |
| `PHASE-8` | Old roadmap phase. Retired: old roadmap phase. | `roadmap.md` |
| `PHASE-9` | Old roadmap phase. Retired: old roadmap phase. | `roadmap.md` |
| `PHASE-10` | Old roadmap phase. Retired: old roadmap phase. | `roadmap.md` |
