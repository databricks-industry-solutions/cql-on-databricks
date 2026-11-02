# Compiler requirements

The compiler's requirements, including its client (`cql-databricks`). Requirements that span features, and the rule for where a requirement lives, are in [`docs/requirements.md`](../../docs/requirements.md). IDs are mnemonic and decoupled from document section numbers. The one-line statement here is the index entry; the full claim and rejected alternatives live in the linked source, relative to this folder. Cite ids per `PROC-CITE-REQ`. When you add or change a requirement, update both its topic doc and this table.

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
| `LANG-AGGREGATE-FUNCTIONS` | The CQL aggregate functions (`Sum`, `Min`, `Max`, `Avg` and the rest) reduce a list per CQL: nulls ignored; null for an empty or all-null list except `Count` (0), `AllTrue` (true) and `AnyTrue` (false); CQL ordering for `Min`/`Max`; quantity units converted. | `language-semantics.md` |
| `LANG-INTERVAL` | Precision-aware temporal comparison and full interval (Allen-relation) algebra. | `language-semantics.md` |
| `LANG-INTERVAL-PRECISION` | An Allen relation naming a precision truncates both operands to it before comparing. | `language-semantics.md` |
| `LANG-DATETIME-COMPONENT` | `year from` and the other component extractors return one component of a date, time or date-time, null when less precise. Not built. | `language-semantics.md` |
| `LANG-DATETIME-OFFSET` | A returned date-time with no offset of its own carries the evaluation request's offset, as the reference does. Not built. | `language-semantics.md` |
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
| `LANG-NO-IMPLICIT-INCLUDE` | The compiler translates a library exactly as written and accepts it when the reference translator does; no added includes. The reference translator is given the same library sources and no library from its classpath (`LANG-FHIRHELPERS-FROM-CONTENT`). | `language-semantics.md` |
| `LANG-FHIRHELPERS-FROM-CONTENT` | FHIRHelpers is content, not part of the compiler. `include FHIRHelpers version 'v'` resolves like any include: from the library sources the caller supplies (cells, the library folder, the environment's knowledge), by name and exact version. The compiler bundles no library and never consults a library source on its classpath (the cqframework `quick` jar's `FhirLibrarySourceProvider`). An include that no supplied source declares fails the translation, naming the library and version. | `language-semantics.md` |
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
| `DATA-RETRIEVE-VIEW` | SUPERSEDED by `BIND-CALL-SITE` and `BIND-CONTRACT`: a retrieve reads the relation the request binds to its call site, by rule or per site, on the contract `(__subject_id, __resource)`; there is no `retrieve_<Type>` view or function, and a retrieve the request does not bind fails, naming its site. | `engine-design.md` |
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
| `DATA-SUBJECT-KEY` | `__subject_id` is normalized top-level key attached at `DataProvider` boundary, not derived mid-hot-path. A resource is patient-context when it has a `subject`, `patient` or `beneficiary` reference (`FhirModel.SubjectReferenceFields`), the same fields the client's reshape reads. | `data-format.md` |
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
| `CONF-GATE-MEASURE` | Gate 3: whole-measure differential against Java CQF engine, patient-by-patient. The corpus measures are written for this repository, and the measurement period is passed to both engines as a run-time parameter, never patched into the CQL. | `testing-and-conformance.md` |
| `CONF-MEASURE-LINK` | Multi-library eCQM flattened into one self-contained library before lowering. | `testing-and-conformance.md` |
| `CONF-GATE-SCOREKEY` | Gate 4: whole-measure scorekey diff — engine per-patient population membership reduced to MeasureReport count must equal answer key. The answer key is hand-written per patient for the repository's own measures, not a published MeasureReport. | `testing-and-conformance.md` |
| `CONF-GOLDEN-PLAN` | Golden snapshots surface optimizer/normalization changes as code-review diffs. | `testing-and-conformance.md` |
| `CONF-REF-DEVIATION` | A divergence from Java CQF reference admissible only as DECLARED, JUSTIFIED deviation on the fixture. | `testing-and-conformance.md` |
| `CONF-GATE-EMIT-INVARIANT` | Server-free, data-free type invariants on emitted SQL of every measure. | `testing-and-conformance.md` |
| `CONF-PERF-TIME` | Perf gate on CPU-vs-wait timing model. | `testing-and-conformance.md` |
| `CONF-PERF-BARRIER-DETAIL` | Extends perf-time to barrier granularity. | `testing-and-conformance.md` |
| `CONF-GROUND-TRUTH` | Reference in-memory providers live in `:compiler:cqf`, the conformance oracle. | `testing-and-conformance.md` |
| `CONF-TESTDATA` | Test data: synthetic FHIR + measure decks + targeted edge fixtures. Measure decks are written for this repository. | `testing-and-conformance.md` |
| `CONF-REF-ANSWER-KEY` | Spark-free gate scoring Java CQF reference against each measure's hand-written `answer-key.json` (per patient, per population), the same key `CONF-GATE-SCOREKEY` reads; a mismatch fails rather than cancels. | `testing-and-conformance.md` |
| `CONF-MEASURE-DISCOVERY` | The measure gates discover the corpus measures; none is listed in code. A measure is a directory `data/measures/<library>/` holding one `<Type>.ndjson` per resource type and an `answer-key.json` that names the library, its version, its measurement period and its populations (population define to population code). The include closure comes from the translated ELM. Adding or removing a measure changes no spec. | `testing-and-conformance.md` |
| `CONF-REF-PROFILE` | Reference retrieve provider honors a retrieve's PROFILE against `meta.profile`. | `testing-and-conformance.md` |
| `CONF-VERIFIER` | Verifier passes assert post-conditions between compiler stages in test/debug builds. | `testing-and-conformance.md` |
| `CONF-TEST-CONVENTIONS` | Conventions for writing tests. | `testing-and-conformance.md` |
| `CONF-CERTIFICATION` | Certification alignment with external digital quality measure certification programs. On hold: certification runs official measures, which this repository does not ship. | `testing-and-conformance.md` |
| `CONF-TEST-LAYERS` | Tests tiered by Spark dependency: L1 semantic / L2 plan-shape / L3 result-equivalence / L4 scale. | `testing-and-conformance.md` |
| `CONF-SPARK-CONNECT` | Tests and production drive Spark through Spark Connect client. | `testing-and-conformance.md` |
| `CONF-SCALE-REMOTE` | Same Connect client targets always-on/serverless Databricks endpoint for warm loop and L4 scale. | `testing-and-conformance.md` |
| `CONF-TEST-ARTIFACTS` | Per-operator test cases capture inspectable artifacts under `build/test-artifacts/`. | `testing-and-conformance.md` |

## BIND — data binding / adapter layer

| id | statement | source |
|----|-----------|--------|
| `BIND-PROVIDER` | SUPERSEDED by `BIND-RELATION`: a retrieve reads the relation its data provider returns, a table, view or SQL function, not only a client-supplied UDTF. | `data-binding.md` |
| `BIND-CONTRACT` | Every binding yields fixed model-derived mount schema `(__subject_id, __resource)`. | `data-binding.md` |
| `BIND-RELATION` | A data resolver answers a call site with a relation: it names where the rows are and carries no field mapping of its own; mapping lives in the view, the pipeline or the DataFrame. A `TableRelation` names a Delta table or UC view returning the contract (`BIND-CONTRACT`), on a SQL warehouse and on Spark. A `VolumeRelation` names a volume folder of `<Type>.ndjson` files or FHIR Bundles, read with `read_files`, on both. A `DataFrameRelation` is a DataFrame, on Spark only. There is no function relation: no UC SQL or Python table function is bound. The client checks a volume or DataFrame relation (`BIND-SCHEMA-CHECK`), reshapes it (`BIND-RESHAPE`) and registers it as a temporary view under a name it owns, on the session that runs the SQL, so the compiler is sent only table names. A DataFrame relation on a SQL warehouse run fails before the run, naming the resolver. Nothing is materialized. | `data-binding.md` |
| `BIND-RETRIEVE-REQUEST` | A data resolver that binds per site is asked for each retrieve call site the compiled CQL makes (`BIND-CALL-SITE`), the context's own retrieve included, with one `RetrieveRequest`: `fhir_type`, `value_set`, `codes`, `date_property`, `date_low`, `date_high` and `parameters`, valued as `BIND-PUSHDOWN` computes them, and the site's context. A new field gets a default, so an existing resolver keeps working; a retired field stays and is ignored. A resolver may ignore every field but `fhir_type`, since the engine re-applies the filters (`BIND-CAPABILITY`). The relation it returns must hold every resource of `fhir_type` that matches the request: a relation missing one gives a wrong result with no error. Its rows depend only on the request, the site and the resolver's data, never on session state or the caller. A site's context names the library the retrieve is written in, never the root being compiled, so a define's SQL is the same whichever library includes it (`EMIT-ROOT-INDEPENDENT`). | `data-binding.md` |
| `BIND-NAMED-ARGS` | SUPERSEDED by `BIND-RELATION` and `BIND-PUBLISHED-VIEW`: there is no function relation, so nothing is called with arguments and no parameter is read from `information_schema`. A published view is read by name, and the compiled plan applies the request's filters above it. | `data-binding.md` |
| `BIND-PROVIDER-REQUEST` | SUPERSEDED by `BIND-CALL-SITE`: the compile request binds each role by a rule, a map by call site, or both, and `describe` reports every call site, not only retrieves. | `data-binding.md` |
| `BIND-CALL-SITE` | The compiler reports call sites and binds them from the request; it never calls a resolver, reads the catalog or knows of environments, users or results (`ARCH-PURITY`, `SVC-STATELESS`). `describe` returns every call site: each data retrieve with its `RetrieveRequest` (`BIND-RETRIEVE-REQUEST`), each value-set reference with the value set's id and version, and each library include with the included name and version; each with the name and version of the library it is written in and the define it is in, none outside a define. A site's id is its kind and 12 hex digits of a hash of that context, its position in the define and its request, so it is the same whichever root includes the library. The compile request carries one field per role. `data` and `terminology` each carry a `rule`, a `sites` map from site id to relation, or both; for each site the compiler uses the map's entry if there is one, else the rule. A data rule `{"schema": s, "identity": i}` binds a retrieve of type `T` to the view `s.<T in lower case>`; a data site entry is `{"table": name, "identity": i}`. A terminology rule or site entry is a list of value-set relations of `(valueset_id, version, system, code)`, each with its identity, in precedence order: a value set is read from the first that holds it. `knowledge` is the root's include closure (`BUILD-COMPILE-LINK`), which the client resolves and may resolve per include site; one name and version is one library in a compile, and two different answers for it fail. `runtime.schema` is the schema of the `cql_*` functions (`EMIT-UDF-QUALIFY`). The compiler embeds each relation in the SQL it emits, so which relation each site used is in the SQL and its fingerprint. A site with no relation, or a membership test with no terminology, fails the compile, naming it. A run whose resolvers all bind by rule makes one compiler call; only a resolver that binds per site calls `describe` first. The client never patches the SQL the compiler returns. A request that binds no data compiles CQL that retrieves nothing; a retrieve in it fails, naming its site, and a membership test with no terminology fails the same way. `describe` lowers over placeholder relations, named in no SQL that runs, so it needs no binding. A request carrying `sources` or `dataSource` is refused, naming the field; a request with roles has no `deps` or `libraries`. | `data-binding.md` |
| `BIND-PUBLISHED-VIEW` | A run binds an environment's published views by name, through each role's rule (`BIND-CALL-SITE`): data from `<catalog>.data.<type>`, terminology from `<catalog>.terminology.valueset`, knowledge from `<catalog>.knowledge.library` and `.measure` (`DATA-ENVIRONMENT-LAYOUT`). It reads no `information_schema` and never checks a store; a missing view fails in the warehouse, naming it. Publishing writes rows to the store tables, as the caller, then reads the written ids back through the published view once; ids the view does not show fail the publish, saying the view does not select the store. An environment with no store schema refuses a publish, naming the schema. | `data-binding.md` |
| `BIND-RESHAPE` | One implementation turns FHIR data into the mount contract: generated SQL over a named relation (`cql_databricks.compiler.reshape`), used by the volume and DataFrame data resolvers (`BIND-RELATION`), the publish step and the loader. JSON text is parsed against the model's struct; a structured relation is rebuilt field by field, a missing field a typed null, an extra one dropped, a safe widening cast. `__subject_id` is `id` for Patient, the trailing id of `subject`, `patient` or `beneficiary` `.reference` for a Patient-compartment type, else null. No SQL text is taken from a caller. | `data-binding.md` |
| `BIND-SCHEMA-CHECK` | A source is checked against the model before a volume or DataFrame relation over it is bound to a run (`BIND-RELATION`) or it is published, naming every problem at once: a missing field WARN, a safe widening or a dropped field INFO, and as ERROR a type no cast fixes safely, a merged choice field (`value` for `valueQuantity`…), a reference or other struct stored as a JSON string, no `id`, or no way to derive `__subject_id`. A source with an ERROR is refused. Checking the fields one query demands is not built. | `data-binding.md` |
| `BIND-PUBLISH` | The client publishes data over tables into an environment (`publish_data_source`), as the caller, writing a mapping and never data: one published view per type in the environment's `data` schema (`DATA-ENVIRONMENT-LAYOUT`), `CREATE OR REPLACE VIEW <catalog>.data.<type>` with the reshape of its table as the body (`BIND-RESHAPE`), replacing the view that selected the store. It returns a plan with the SQL and every check finding; `run()` refuses on any ERROR (`BIND-SCHEMA-CHECK`). Sources are `catalog.schema.table` names only; a DataFrame or `FhirJson` is refused, naming the fix. Each view returns its whole table, a correct superset (`BIND-CAPABILITY`). | `data-binding.md` |
| `BIND-MANIFEST` | SUPERSEDED by `BIND-PROVIDER-REQUEST`: the request's `data` field binds each retrieve, not each type, and `sources`, with its `valuesets` and `udfs` keys, is removed. | `data-binding.md` |
| `BIND-DATA-SOURCE-LAYOUT` | SUPERSEDED by `DATA-ENVIRONMENT-LAYOUT`: data is an environment's `data` schema of published views named for their type, not a tagged schema of `retrieve_<Type>` objects. | `data-binding.md` |
| `BIND-RETRIEVE-UDF` | SUPERSEDED by `BIND-RELATION` and `BIND-NAMED-ARGS`: a retrieve may read a table or view as well as a function, and a function declares only the parameters it uses. That a relation's rows depend only on its request and its data is `BIND-RETRIEVE-REQUEST`. | `data-binding.md` |
| `BIND-UC-PRECREATE` | SUPERSEDED by `BIND-PUBLISHED-VIEW`: published data is views, bound by name; no retrieve function is created. | `data-binding.md` |
| `BIND-PUSHDOWN` | Engine computes each retrieve's statically known predicates and parameters as its `RetrieveRequest` (`BIND-RETRIEVE-REQUEST`), for a resolver to choose a relation by. A relation takes no arguments: the compiled plan applies the filters above it, and Catalyst pushes them into the scans under a view. | `data-binding.md` |
| `BIND-EXPLICIT-PARAMS` | SUPERSEDED by `BIND-RETRIEVE-REQUEST` and `BIND-NAMED-ARGS`: seven positional parameters meant removing one broke every function. A function now declares by name only the request fields it uses. `code_property` is not a request field. | `data-binding.md` |
| `BIND-CAPABILITY` | Pushdown is optimization not correctness; a resolver honors as much of the request as it can and the engine re-applies every filter. | `data-binding.md` |
| `BIND-TERMINOLOGY` | Value sets enter via TerminologyProvider.expand as pre-expanded, version-pinned tables. | `data-binding.md` |
| `BIND-TERMINOLOGY-TABLE` | SUPERSEDED by `DATA-ENVIRONMENT-LAYOUT` and `BIND-PUBLISHED-VIEW`: terminology is the environment's published view `terminology.valueset(valueset_id, system, code, version)`, not a schema tagged `cql_databricks_terminology`. | `data-binding.md` |
| `BIND-ESCAPE` | Hand-written JVM `DataProvider` remains a supported power-user escape hatch. | `data-binding.md` |
| `BIND-REUSE` | SUPERSEDED by `BIND-RESHAPE` and `BIND-PUBLISH`: shaping SQL is generated once, by the reshape, and becomes a published view's body; there is no UDTF arm to call it. | `data-binding.md` |
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
| `PIPE-SQL-DAG-REUSE` | `/compile-pipeline` lowers the whole measure once, then cuts the DAG at each materialized define into one dataset recipe. A durable plan cuts at every define (`materialize.mode=all`) and never at a shared retrieve read (`materialize.retrieves=none`), whose selection counts call sites across the closure. | `pipeline-runtime.md` |
| `PIPE-DURABLE-IDENTITY` | Opt-in durable plans expose one `fingerprint` per recipe (format `cql-result-identity-v1`, `RUN-CLIENT-RESULT-IDENTITY`), over its query, its parameter values, its upstream fingerprints and the identities of the data and terminology resolvers whose relations the compile binds, by rule or by site (`CLIENT-RESOLVER-IDENTITY`). The compile response lists the value sets each recipe reads, and the client completes each fingerprint with the digests of those value sets' codes, upstream first, since the compiler reads no catalog. A durable compile that retrieves data with no data identity fails. | `pipeline-runtime.md` |
| `PIPE-EVAL-SCOPED-READ` | Pipeline compile request may name evaluation set; each recipe reads published sibling scoped to that evaluation. | `pipeline-runtime.md` |
| `PIPE-READS-SORTED` | A recipe's `reads` (published siblings it reads) is emitted sorted, not in walk order. | `pipeline-runtime.md` |
| `PIPE-MULTI-LIB` | SUPERSEDED by `BIND-PROVIDER-REQUEST`: no caller sent several roots. A request has one root and its include closure in `knowledge`. | `pipeline-runtime.md` |
| `PIPE-MULTI-DESCRIBE` | SUPERSEDED by `BIND-PROVIDER-REQUEST`, with `PIPE-MULTI-LIB`. | `pipeline-runtime.md` |
| `PIPE-EMIT-SQL` | `/compile-pipeline` returns complete, deploy-ready SQL for each recipe in its `sqlFile` field (`CREATE OR REFRESH MATERIALIZED VIEW`), so it runs with no runtime compile. | `pipeline-runtime.md` |

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
| `EMIT-DESCRIBE-RETRIEVE-TYPES` | `describe` lists every resource type the library retrieves, sorted, including a retrieve that navigates no field and the context's own retrieve; empty for data-free CQL. A caller binds exactly the data a run needs (`CLIENT-DATA-RESOLVER`). `describe` also lists every call site with its id and context (`BIND-CALL-SITE`). | `compiler-service.md` |
| `EMIT-ROOT-INDEPENDENT` | A define's emitted SQL, its recipe name and whether it is materialized are the same whichever library is the root, so a library that includes another reuses the included library's stored results (`DEC-LIBRARY-OWNS-RESULTS`, `RUN-CLIENT-RESULT-IDENTITY`). No root library name appears in the SQL: measure selection is the data-source binding, and no retrieve request field names a library (`BIND-RETRIEVE-REQUEST`). Names come from the owning library and version, never the root. Materialization cut points belong to the define, not the closure. The pipeline compile names the root's own defines by their library as the linker names an included library's (`LibraryLinker.ownerQualified`), so every object and alias is `<library>_<define>`, and names each recipe `<owner>_<owner version>__<define>` with that owner as its origin. A population is a data source (`DATA-CONFORMANCE-MEASURE-SELECT`). Parameter claims and the retrieve request's `parameters` use each define's own library's parameter names (`ParameterScope`; HL7 CQL Library > Parameters: an included library's parameter takes the root's value when the root declares the same name, else its own default). Describe and the session compile keep the authored names they publish. Checked by `IncludedReuseSpec`. | `pipeline-runtime.md` |
| `EMIT-SQL-RETRIEVE-DEMAND-BOUND` | Retrieve's scanned resource bounded by DEMAND, not resource width. | `execution-model.md` |

## BUILD — CQL Builder: modules, client, compile service

| id | statement | source |
|----|-----------|--------|
| `BUILD-CLIENT-NAMESPACE` | `cql-databricks` ships `cql_databricks.compiler` and `cql_databricks.data_bindings`. | `../../docs/systems.md` |
| `BUILD-CLIENT-SCOPES` | The client holds `compile`, `run` and `materialize` (`cql_databricks.result`); its mode is fixed when constructed. The resolvers a compile binds are resolved at each compile (`CLIENT-RESOLVERS`). | `../../docs/systems.md` |
| `BUILD-MODULES` | Gradle projects: `compiler/{core,service,server,runtime}`, conformance, `common`, cql-databricks Python. | `modules.md` |
| `BUILD-COMPILE-SERVICE` | Compiler-service logic + OSS host live in-repo; `/compile-sql`, `/compile-pipeline`, `/describe`. | `pipeline-runtime.md` |
| `BUILD-COMPILE-LINK` | Compile endpoints accept the root and its dependency libraries in `knowledge` (`BIND-CALL-SITE`), each ELM or CQL, and flatten them via `LibraryLinker`. The caller resolves the include closure from the registry and sends it on each compile request. The linked library records each merged name's owning library and version (`Library.owners`). | `pipeline-runtime.md` |
| `BUILD-CLIENT` | Python `cql-databricks` exposes `cql(library, target, boundaries)` + `load_library` + provider. | `pipeline-runtime.md` |
| `BUILD-RESOLVE` | Client resolves every setting by one precedence: explicit arg → spark.cqldatabricks.* → CQL_DATABRICKS_* env → compiled-in default → fail. No setting is discovered: the data a run reads is chosen at compile time by its environment or the resolvers passed in (`CLIENT-ENVIRONMENT`). Serverless refuses to set a custom Spark conf key (`CONFIG_NOT_AVAILABLE`), so notebooks and Jobs pass settings as `connect()` keywords or env. A new setting gets a keyword and an env var, never a `spark.cqldatabricks.*` key. | `pipeline-runtime.md` |
| `BUILD-ENV` | Client session is CQL builder; each binding dual-represented. | `pipeline-runtime.md` |
| `BUILD-TYPE-METADATA` | Define's declared result type is authoritative metadata, distinct from physical columns. | `pipeline-runtime.md` |
| `BUILD-VERSION-PIN` | Deployed pipeline compiles against pinned compiler + terminology version. | `pipeline-runtime.md` |
| `BUILD-CLIENT-SESSION` | Session holds endpoint/auth/resolvers/context/data-model resolved through BUILD-RESOLVE. | `pipeline-runtime.md` |
| `BUILD-CLIENT-SESSION-PARAMETERS` | Client session may set parameter values once, for every query; service applies only those the library declares. | `pipeline-runtime.md` |
| `BUILD-CLIENT-QUERY` | `cql(source)` returns lazy `CqlQuery` handle; `source` is raw CQL/ELM/resource path or bound `Library`. | `pipeline-runtime.md` |
| `BUILD-CLIENT-POPULATIONS` | `exec()` with no targets defaults to measure population defines. | `pipeline-runtime.md` |
| `BUILD-LIBRARY-PROVIDER` | Library source resolution is client-side seam; OSS ships `FileLibraryProvider` over search path. | `pipeline-runtime.md` |
| `BUILD-RESOLVE-COMPILE` | One resolution for every caller: `cql_databricks.compiler.resolve`. | `compiler-service.md` |
| `BUILD-CLIENT-COMPAT` | Client↔service wire bidirectionally tolerant; Python surface append-only. | `client-compatibility.md` |
| `BUILD-META` | Client returns first-class metadata: `describe(lib) → LibraryGraph`. | `pipeline-runtime.md` |
| `BUILD-URL-KEBAB` | Every HTTP endpoint path lowercase kebab-case (no camelCase or underscores in path segments). | `pipeline-runtime.md` |

## SVC — compile service, notebooks and runtime functions

| id | statement |
|----|-----------|
| `ACC-NAMESPACE-ALIGN` | Accelerator code under com.databricks.cql.platform with distinct product tokens; engine rename to add com. prefix is engine-led. |
| `SVC-API-HOST` | The Compiler Service provides a network endpoint hosting the engine's compile APIs via HTTP. |
| `SVC-BINDING-RUNTIME` | Data and terminology are bound at execution time in the client session, not at compile time. |
| `SVC-COMPILE-BATCH` | A durable preparation makes one compile-service request when its resolvers bind by rule, and two, `describe` and then the compile, carrying the same request, when one binds per site (`BIND-CALL-SITE`); it fetches nothing else from the compiler. Dependency ELM is retained and callers are identified explicitly. |
| `SVC-COMPILE-CACHE` | Process-local LRU cache for successful compilation responses, keyed by build, operation, and request content. |
| `SVC-COMPILE-ENDPOINT` | One HTTP endpoint for both interactive authoring compiles (translate, validate, plan, explain) and runtime compiles. |
| `SVC-COMPILER-IDENTITY` | Version identifier for the deployed compiler, published to signal when recomputation is needed after compiler updates. |
| `SVC-COMPILER-M2M` | SUPERSEDED by `SVC-EXCHANGE-RETRY`: a Job reaches the Compiler App with its own identity through the token exchange, which retries the intermittent `invalid_grant`. The deploy provisions no compiler caller, and the client rejects the retired `compilerAuth`, `compilerClientId`, `compilerSecretScope` and `compilerSecretKey` settings. The caller lookup by `externalId` never matched: the workspace SCIM API does not keep that field. |
| `SVC-DISCOVERY-PLUGIN` | Databricks plugin that resolves the client's compiler discovery, using app naming convention and OAuth token exchange. |
| `SVC-EXCHANGE-RETRY` | The Databricks plugin's App token exchange retries `invalid_grant` with backoff for up to 3 minutes, logging one warning per retry with the error code only, then fails with the usual error. A workaround for an intermittent platform failure in Jobs; the generic exchange does not retry unless a plugin opts in. |
| `SVC-NOTEBOOK-CELL-LIBRARY` | A measure can be authored across notebook cells via a `%%cql` cell magic and mounted library directories. The declared cells belong to the Spark session (`CLIENT-SESSION-STATE`), not the Python process. A cell with a `library` header is its own library; one without joins the session library (`SVC-NOTEBOOK-SESSION-SCOPE`). |
| `SVC-NOTEBOOK-CQL-MAGIC` | Cell magic `%%cql` registers with the Python extension and enables measure authoring in notebooks. A cell with no `library` header joins the session library and runs only its own defines (`SVC-NOTEBOOK-SESSION-SCOPE`). |
| `SVC-NOTEBOOK-LIBRARY-FOLDER` | A notebook mounts one or more directories of .cql files as its library source via `connect(library_path=...)`, each read as a folder location reads (`RUN-ADDRESS-LOCATION`). Library references resolve in one order, an overlay chain of knowledge resolvers (`CLIENT-RESOLVER-OVERLAY`): `%%cql` cells, then the mounted directories, then the environment's knowledge (`CLIENT-ENVIRONMENT`, `CLIENT-KNOWLEDGE-INCLUDES`). A library from the environment's knowledge resolves its includes there only. |
| `SVC-NOTEBOOK-RUNTIME-BIND` | Runtime bindings: discover UDF schema from the registry, rewrite serverless barriers, no `%scala` cell needed. |
| `SVC-NOTEBOOK-SESSION-SCOPE` | CQL with no `library` header, from a `%%cql` cell or a `cql("...")` string, joins one library per Spark session. A later fragment sees earlier defines unqualified, and redeclaring a name replaces it. `using` follows the translator: an identical repeat is accepted; a second version of a model is an error. A fragment joins only if the whole library still compiles. `reset(spark)` clears it. The compiler merges the fragments (`merge-cql`). |
| `SVC-REF-PROVIDERS` | Supply reference-only DataProvider and TerminologyProvider to the compile service. |
| `SVC-RESOURCE-SCHEMA` | Compiler exposes POST /resource-schema to query the modeled structure of FHIR types. |
| `SVC-RUN-SERVERLESS-BARRIER` | On serverless, rewrite cache table statements to temporary views before execution. |
| `SVC-RUNTIME-UDF` | Deploy cql_* UC catalog and register on the runtime cluster as cluster-scoped libraries. |
| `SVC-RUNTIME-UDF-IDENTITY` | Tracks which build of the engine's `cql_*` UC functions is registered, triggering full refresh when UDFs are re-registered despite unchanged compilation output. |
| `SVC-RUNTIME-UDF-UC` | CQL operators register as UC Scala functions on serverless, SQL warehouses, and all compute types via manifest-derived DDL and dependencies staged in a runtime volume. |
| `SVC-SPLIT` | Authoring compile is Spark-free and interactive; runtime compile is Spark-free and per-pipeline-update. |
| `SVC-STATELESS` | The Compiler Service is a pure function: no session, no user data, no state held between requests. |
| `SVC-ZERO-CONFIG-NOTEBOOK` | Open a notebook and run cql("...") with no configuration when a single deployment matches the default naming. |

## CLIENT — client sessions, data binding and knowledge

| id | statement |
|----|-----------|
| `CLIENT-EXEC-DISCOVERY` | SUPERSEDED by `CLIENT-DATA-PROVIDER` and `CLIENT-DEFAULT-RESOLVER`: a run reads data and value sets only through providers, and `TaggedResolver` returns a tagged source by name. `connect(data_catalog=)`, `source_binding`, `connect(valueset_table=)` and the runtime `valuesets` view are removed. Whether a notebook that names no source gets the only one in scope is `DEC-NOTEBOOK-DEFAULT-RESOLVER`. |
| `CLIENT-DATA-PROVIDER` | SUPERSEDED by `CLIENT-DATA-RESOLVER`: the role is named `DataResolver`, and there is no function relation. |
| `CLIENT-TERMINOLOGY-PROVIDER` | SUPERSEDED by `CLIENT-TERMINOLOGY-RESOLVER`: the role is named `TerminologyResolver`. |
| `CLIENT-KNOWLEDGE-PROVIDER` | SUPERSEDED by `CLIENT-KNOWLEDGE-RESOLVER`: the role is named `KnowledgeResolver`. |
| `CLIENT-RESULT-STORE` | SUPERSEDED by `CLIENT-RESULT-RESOLVER`: the role is named `ResultResolver`, over an environment's result schemas. |
| `CLIENT-PROVIDER-READ-WRITE` | SUPERSEDED by `CLIENT-RESOLVER-READ-WRITE`: the same split, in resolver terms. |
| `CLIENT-PROVIDER-OVERLAY` | SUPERSEDED by `CLIENT-RESOLVER-OVERLAY`: the same composition, in resolver terms. |
| `CLIENT-PROVIDER-IDENTITY` | SUPERSEDED by `CLIENT-RESOLVER-IDENTITY`: the same rule, in resolver terms, per call site. |
| `CLIENT-DEFAULT-RESOLVER` | SUPERSEDED by `CLIENT-ENVIRONMENT-RESOLVER`: the default resolver finds environments by catalog tag, not data sources, terminology and knowledge by schema tag in catalog scopes. `TaggedResolver` is removed. |
| `CLIENT-ENVIRONMENT` | Every run is in exactly one environment. An environment is a Unity Catalog catalog tagged `cql_databricks_environment = <name>` and `cql_databricks_environment_version = <layout version>`, and on the default one `cql_databricks_environment_default = true`; its description is the catalog comment, kept short. The three tags are governed, so only the project's admins can assign them (`DEPLOY-ENVIRONMENT-TAG-POLICIES`). It holds every dependency of a run, data, terminology, knowledge and results, in the schemas of `DATA-ENVIRONMENT-LAYOUT`. Environments are found with one query of `system.information_schema.catalog_tags`, as the caller, so only catalogs the caller can see are found. A client given nothing runs in the default environment; a notebook or Job may name one (`connect(environment=)`) or pass resolvers. The version says which layout the environment follows, so the client can tell whether it can read it; it is in no result's identity (`RUN-CLIENT-RESULT-IDENTITY`). Nothing else is discovered: no schema tag and no catalog scope. |
| `CLIENT-ENVIRONMENT-RESOLVER` | `EnvironmentResolver` finds environments (`CLIENT-ENVIRONMENT`), lists them, and returns the default or one by name; for it, it returns one resolver per role, each binding by rule (`BIND-PUBLISHED-VIEW`). Two visible defaults fail, naming both catalogs; no visible default fails, naming the tag; a name not found fails, naming those that are; a layout version the client does not support fails, naming it and the versions it supports. The Designer, the agent and Genie's MCP tools resolve through it, on a SQL warehouse, as the user. |
| `CLIENT-RESOLVERS` | Every dependency of a run reaches it through a resolver, one interface per role: `DataResolver` (`CLIENT-DATA-RESOLVER`), `TerminologyResolver` (`CLIENT-TERMINOLOGY-RESOLVER`), `KnowledgeResolver` (`CLIENT-KNOWLEDGE-RESOLVER`) and `ResultResolver` (`CLIENT-RESULT-RESOLVER`). Every implementation's name ends in `Resolver`; `EnvironmentResolver` returns one of each for an environment (`CLIENT-ENVIRONMENT-RESOLVER`). The interfaces live in the client core, which imports no platform SDK. A custom resolver runs where its code runs, in notebooks and Jobs; the Apps use `EnvironmentResolver`. |
| `CLIENT-DATA-RESOLVER` | Data reaches a run only through a `DataResolver`: `identity()` (`CLIENT-RESOLVER-IDENTITY`), `reusable` (false for a development resolver), and either `rule()`, the schema whose views serve every type (`BIND-CALL-SITE`), or `types()`, the FHIR types it serves, and `retrieve(request)`, a relation for one call site (`BIND-RETRIEVE-REQUEST`, `BIND-RELATION`); and `change_log()`, a relation or none (`DATA-CHANGE-LOG-PERIODS`). A retrieved type outside `types()` fails before the run, naming the type and the resolver; a type a rule's schema has no view for fails in the warehouse. Shipped: the environment's published views, by rule; a Delta table or view per type; a volume folder of `<Type>.ndjson` files or Bundles; and a DataFrame per type (Spark only, not reusable). A workspace folder is never a data resolver. |
| `CLIENT-TERMINOLOGY-RESOLVER` | Value sets reach a run only through a `TerminologyResolver`: `identity()`, either `rule()`, the relations every value set is read from, or `expansions(site)`, the relations for one value-set reference (`BIND-CALL-SITE`), each of `(valueset_id, version, system, code)`; and `find_code(code, system)`, the value sets that contain a code (`AGENT-VALUE-SET-LOOKUP`). A `TerminologyStore` adds `publish(value_sets)` (`BIND-PUBLISHED-VIEW`). Shipped: the environment's `terminology.valueset` view, by rule, whose store is `terminology_store`; FHIR `ValueSet` JSON, in a volume (both computes) or a workspace folder (Spark only); and a DataFrame of `(valueset_id, version, system, code)` rows, for development (Spark only). A value set must carry its expansion: one with only `compose` fails, naming it. |
| `CLIENT-KNOWLEDGE-RESOLVER` | Libraries and measures reach a compile only through a `KnowledgeResolver`, looked up at compile time: `identity()`, `library(name, version, site)`, its CQL and ELM or none, given the include site that asks (`BIND-CALL-SITE`), `measure(url, version)`, a Measure resource or none, and `libraries()`, every library it holds. A `KnowledgeStore` adds `publish(closure)`, which is `CLIENT-PUBLISH`. The include closure is resolved in one place in the client core (`KNOWLEDGE-RESOLVE-API`); a resolver only answers lookups. Shipped: the session's `%%cql` cells, a folder (`/Workspace` or `/Volumes`, `RUN-ADDRESS-LOCATION`) and the environment's `knowledge` views, whose store is `knowledge_store`. |
| `CLIENT-RESULT-RESOLVER` | Kept results are found and written through a `ResultResolver`: `find(fingerprint)` returns the table holding that result or none, and `write(recipe, relation)` writes it and returns its table. The shipped one is Delta, over the environment's `results` schema, for published runs of Jobs and notebooks, and its `draft_results` schema, for the Designer, the agent and every other run (`RUN-CLIENT-DRAFT-RESULTS`), with the tags and names of `RUN-CLIENT-RESULT-IDENTITY` and `RUN-CLIENT-TABLE-NAMES`; `find` returns only a table its caller owns (`RUN-CLIENT-RESULT-OWNERSHIP`). A result is never written to a folder. |
| `CLIENT-RESOLVER-READ-WRITE` | Reading and writing are separate interfaces. Code that reads takes a resolver (`DataResolver`, `TerminologyResolver`, `KnowledgeResolver`); `publish` is only on a store (`TerminologyStore`, `KnowledgeStore`), which writes the store tables (`BIND-PUBLISHED-VIEW`), so publishing through a read-only resolver is a type error. One class may implement both. Data has no `publish`: customers own their tables, and `BIND-PUBLISH` writes a published view, never data. A `ResultResolver` both finds and writes. |
| `CLIENT-RESOLVER-OVERLAY` | `Overlay(dev, base)` composes two resolvers of one role, the first that has a thing serving it: per FHIR type for data, per value set for terminology, per library for knowledge. The knowledge lookup order (`SVC-NOTEBOOK-LIBRARY-FOLDER`) is an explicit overlay chain. An overlay's `reusable` is false when any layer's is, and its identity is its layers' identities in order. |
| `CLIENT-RESOLVER-IDENTITY` | A kept result's fingerprint covers the identities of the data and terminology resolvers whose relations its compile binds, by rule or per site, and the codes of every value set it, or any define it reads, uses (`PIPE-DURABLE-IDENTITY`). An identity names what a resolver reads, never who reads it; an environment's rule has its schema, `<catalog>.data` or `<catalog>.terminology`, as identity. A value set's codes enter as a digest of its sorted `(system, code)` rows and its version, read from the relation the run binds in one query per kept run, so a value set edited in a volume or republished in place gets a new fingerprint and an unchanged one keeps its own. Data enters by identity only: data that changes in place is served as of now (`RUN-CLIENT-RESULT-IDENTITY`). A run with any resolver whose `reusable` is false is never reused and never kept. |
| `CLIENT-KNOWLEDGE-INCLUDES` | `cql()` resolves a library's includes from the session's knowledge resolvers in the order of `SVC-NOTEBOOK-LIBRARY-FOLDER`: `%%cql` cells, then the mounted folders, then the environment's knowledge (`CLIENT-KNOWLEDGE-RESOLVER`). A library from the environment's knowledge resolves its includes there only. An include none of them holds fails, naming the library and version (`LANG-FHIRHELPERS-FROM-CONTENT`). |
| `CLIENT-INLINE-WAREHOUSE` | `cql_databricks.result.run_inline` runs a CQL library inline on a SQL warehouse session and keeps nothing: it compiles through `/compile-sql` with the run's resolvers (`CLIENT-RESOLVERS`) and period, creates only temporary views under a prefix of its own (`EMIT-SQL-OBJECT-PREFIX`) so two runs in one held session never replace each other's, counts what is asked, and drops every view it created, including after a failed statement. A `CACHE TABLE` barrier is refused, not rewritten. Each run reads the data as it is now; nothing is tagged or reused. |
| `CLIENT-PUBLISH` | `cql_databricks.compiler.publish_folder_library` writes a library from a workspace folder into an environment's knowledge store (`BIND-PUBLISHED-VIEW`), as the caller: its `Library`, with the CQL and the ELM compiled from it, and, for a measure, its `Measure`. Its includes that resolve from the folder are published with it, dependencies first; includes resolved from the environment's knowledge are left alone. A library, the root included, is written only when it changed: the store has no row for its `(url, version)`, or the row's CQL differs from the folder's (line endings aside), or, for a measure, its `Measure` differs. Unchanged libraries are reported and not written. Every library to be written must compile first; if any does not, nothing is written (`PublishRefused`). Rows go through the import's MERGE (`CONTENT-INCREMENTAL`), so a published `(url, version)` is replaced (`DEC-PUBLISH-IMMUTABLE`). After the writes it reads the written `(url, version)` pairs back through the `knowledge` view once; one the view does not show fails the publish (`BIND-PUBLISHED-VIEW`). A library's URL is its folder `Library`'s `url`, else `<base>/Library/<Name>`. Writes to separate rows are not one transaction: a failure leaves the libraries before it written, and `PublishFailed` says which. It runs through any SQL runner and compile the caller passes: the Designer passes a warehouse session as the user (`DOC-PUBLISH`), and `cql_databricks.compiler.publish_library` passes a notebook's or Job's Spark session, its `/Workspace` files, its environment and the Compiler Service App. |
| `CLIENT-RESOURCE-SCHEMA` | The client exposes the compiler's FHIR model: `CqlClient.resource_schema(fhir_type=None)` returns each modeled type's `__resource` struct as a PySpark `StructType` and DDL, and whether it is in the Patient compartment. It reads them from `/resource-schema` (`SVC-RESOURCE-SCHEMA`) once per client and never derives a schema itself. An unmodeled type raises, naming it. |
| `CLIENT-SESSION-MOUNT` | SUPERSEDED by `CLIENT-SESSION-RESOLVERS`: a notebook's cells read data and value sets through the session's resolvers, a DataFrame, volume or table resolver among them (`CLIENT-DATA-RESOLVER`, `CLIENT-TERMINOLOGY-RESOLVER`), and a dev override is an `Overlay` (`CLIENT-RESOLVER-OVERLAY`). `mount()` and its temporary functions are removed. |
| `CLIENT-SESSION-RESET` | `reset(spark)` drops the temporary views the client registered for relations (`BIND-RELATION`) and the libraries `%%cql` cells declared, then the session record, and returns the session's resolvers to the environment's (`CLIENT-SESSION-RESOLVERS`). Safe to call twice. Define result views are left alone. |
| `CLIENT-SESSION-RESOLVERS` | A notebook sets the resolvers its `cql()` calls and `%%cql` cells run through with `use_resolvers(data=, terminology=, knowledge=)` (`CLIENT-RESOLVERS`, `SVC-NOTEBOOK-CQL-MAGIC`). Each role given replaces the environment's resolver for that role, and each role not given is the environment's (`CLIENT-ENVIRONMENT`), as with `connect()`. A call sets every role, so `use_resolvers()` with none returns to the environment. A value of the wrong role fails, naming the role. Setting them touches no workspace; the next `cql()` or cell builds its client with them. They are Python objects, so they are held in the notebook's Python process, not the session record (`CLIENT-SESSION-STATE`): restarting Python loses them, as it loses a DataFrame, and `reset(spark)` drops them (`CLIENT-SESSION-RESET`). A client from `connect()` takes its resolvers as arguments and is not changed by them. A cell over them keeps nothing (`RUN-CLIENT-DRAFT-RESULTS`). |
| `CLIENT-SESSION-STATE` | The `%%cql` cells are rows of one temp view, `__cqldatabricks_session`, in the Spark session, so they end with it. Reading with no Spark session finds nothing; declaring a cell with none raises. The client writes no session conf. |
| `CONTENT-RESOLVER` | SUPERSEDED by `CLIENT-ENVIRONMENT-RESOLVER`: environments are found by catalog tag in one query; no tagged schema is searched for. |
| `DATA-BIND-PROGRESS` | SUPERSEDED by `CLIENT-DEFAULT-RESOLVER`, with `DATA-BINDINGS`: there are no knowledge views to bind. |
| `DATA-BINDINGS` | SUPERSEDED by `CLIENT-DEFAULT-RESOLVER`: providers read the tagged schemas directly, in the caller's scopes, at each compile. The `runtime.measures`, `runtime.libraries` and `runtime.valuesets` views and `bind_knowledge_views` are removed: they read the deployer's scopes, not the reader's, as of when they were built, and only `valuesets` had a reader. |
| `DATA-CHANGE-LOG` | A data source declares a `change_data_log` relation with per-FHIR-type change timestamps. The compiler reads it once per run to compute a data fingerprint for every barrier statement, detecting when data moved and triggering recompute. |
| `DATA-CHANGE-LOG-PERIODS` | A data source may declare a `change_log(parameters, changed_at)` relation to signal which evaluation periods changed. The compiler uses it to refresh only those periods; without it, every open period refreshes every run. |
| `DATA-CONFORMANCE-MEASURE-SELECT` | SUPERSEDED by `DATA-SAMPLE-SOURCE`: there is no per-measure conformance source left to select. The example patients are one source every example measure runs over. |
| `DATA-INSIGHT-EXTRACTION` | A curated declaration of `(data model, resource type) -> elements worth surfacing`. Queries generated from catalog, feeding Terminology sections and Demographics cuts. Model is part of key; uncurated pairs yield nothing. |
| `DATA-SOURCE-BIND` | SUPERSEDED by `BIND-PROVIDER-REQUEST`: one compiled plan still binds to any data, through the relations in the request. `connect(data_catalog=)` and the fixed `retrieve_<Type>` names in the SQL are removed. |
| `DATA-SOURCE-BINDING-MODE` | SUPERSEDED by `BIND-RELATION`: a retrieve reads a table, view, function, volume or DataFrame relation. |
| `KNOWLEDGE-CORPUS-REFRESH` | Measure resources load lazily on first access behind an LRU cache; library metadata loads eagerly and refreshes on Delta version movement. Manual reload is available via API. |
| `KNOWLEDGE-RESOLVE-API` | Measure/Library resolve through one resolver composing external content by reference, never by copy. The notebook client and the app resolve byte-identically from the environment's knowledge, feeding the returned closure ELM into the engine as `deps`. An include that pins a version resolves only to that version. Another version of the same name never stands in; a missing pin is reported unresolved. |
| `PERF-CLOSURE-TRANSLATE` | Include closure compilation passes each library only its own declared includes to the compiler, avoiding quadratic re-parsing of accumulated sources. |
| `REG-SOURCE-PROVIDER` | One registry-backed LibrarySourceProvider resolves includes and hands the closure to the engine as input; one resolution path, no gap between publishing and runtime. |

## RUN — durable calculations and results

| id | statement |
|----|-----------|
| `CLIENT-DISCOVERY-SCOPE` | SUPERSEDED by `CLIENT-ENVIRONMENT` and `RUN-CLIENT-DRAFT-RESULTS`: nothing is discovered in catalog scopes. A run reads its environment, and results are read from the environment's `results` and the caller's own draft results. `catalog_scopes` and `CQL_DATABRICKS_CATALOG_SCOPES` are removed. |
| `PIPE-BARRIER-HASH` | SQL hash per materialization barrier determines recompute scope; compiler identity determines recompile scope; UDF identity provides third determinant covering runtime semantic changes. |
| `PIPE-CROSS-SCHEMA-QUALIFY` | When a pipeline's SQL references materialized views owned by different libraries, the BFF fully qualifies those references with `{catalog}.{schema}.{view}` before uploading, handling both `sql_file` (pipeline) and `query` (SQL warehouse). |
| `PIPE-PER-LIBRARY-SCHEMA` | Each published library version writes its own schema `{catalog}.{library}_{version}` with one table per define; CQL that is not published writes to `cql__drafts`; evaluation sets are keyed as rows by `__eval_id`. |
| `PIPE-RESULT-DERIVATIONS` | The client (`cql_databricks.result`) computes derived fields from base results (e.g., numerator flag, result type) during calculation rather than storing them separately. |
| `RUN-ADDRESS-LOCATION` | A library's address is `(location, name, version)`. A location is an environment's knowledge (`store:<catalog>.knowledge`) or a folder (`folder:<path>`); none is the run's environment's knowledge. A store location reads the root from that environment and prefers its copy of each include; a folder location reads through a `LibraryFiles` (the local filesystem, or the Workspace API as the signed-in user), holds every CQL and ELM file under it at any depth outside directories whose name starts with `.`, with a shallower copy ahead of a deeper one, and resolves includes from the folder first, sibling-first, then the environment's knowledge. A calculation request records its location, so a reopen resolves from the same place. |
| `RUN-CLIENT-ASYNC-CHECKPOINTS` | Bound background tag work; invalidate synchronously, respect completion ordering, drain on exit and propagate errors. |
| `RUN-CLIENT-CATALOG-CHECKPOINTS` | Store compact per-table UC success tags with computation-start time and fingerprint; bulk discovery and synchronous invalidation. |
| `RUN-CLIENT-COMPLETION` | Publish reuse evidence only after successful physical write; reject partial results, separate execution state from discovery. A reused result is not pinned to the generations of the results it read; freshness is the data source's (`DEC-RESULT-IDENTITY`). |
| `RUN-CLIENT-DURABLE` | Durable materialization is explicit and caller-authorized; ordinary notebook execution retains its released behavior. |
| `RUN-CLIENT-FACTORED-IDENTITY` | Separate discovery, exact compatibility/storage identity and execution provenance; expose bounded scalar metadata without confusing completion time with freshness. SUPERSEDED by `RUN-CLIENT-RESULT-IDENTITY`: one identity, the fingerprint, serves discovery, compatibility and the table name, and `search_results` filters only by fingerprint and completion time. |
| `RUN-CLIENT-IDENTITY` | SUPERSEDED by `RUN-CLIENT-RESULT-IDENTITY`: the identity covers no destination, owner or compiler version, so one calculation is reused across schemas and users; a compiler change that matters changes the emitted SQL. |
| `RUN-CLIENT-LIFECYCLE` | Automatic successful-computation-age expiry replaces manual archive; private and published storage have separate lifecycles. |
| `RUN-CLIENT-MEASURE-SCORE` | The client scores a measure (`cql_databricks.result.insights.score`), so the Designer, the agent and a notebook report one score. Population defines are counted as written; only the score applies the HL7 CQF Measures population cascade. Proportion: the Numerator within the Denominator (or Initial Population) less the Denominator Exclusions, over the Denominator less the Exclusions and the Exceptions outside the Numerator. Ratio: (Numerator less Numerator Exclusions) over (Denominator less Denominator Exclusions). Cohort: the Initial Population. Continuous-variable is reported unsupported, never approximated. The scoring type is declared (`Measure.scoring` or the caller), never inferred, and a declared population with no result raises. The score carries its own counted numerator and base. |
| `RUN-CLIENT-OPTIMISTIC` | Trust completion metadata and owner-serialized writes; actual SQL errors propagate instead of defensive verification. |
| `RUN-CLIENT-PARALLEL-DEFINES` | A run decides every definition first, in plan order, then computes the ones it must as soon as every definition each reads that the run computes has finished, up to a cap on statements in flight (4 by default, set per run), so a long define delays only the defines that read it. On a warehouse session the statements run side by side in its one session (`execute_async`), sharing its warm sandbox; on a Spark session they run one at a time. One thread does it: it submits every define that is ready, then polls the running ones in turn on the session's connection. The warehouse holds a status request until the statement's state changes or about 5 s pass, so a poll is also the wait, and a finish can be noticed up to about 5 s late while a longer statement is being polled. The run reports the warehouse's own error for a statement that fails. The first failure cancels every statement still running and starts nothing more; each cancelled define is reported with decision `cancelled`. |
| `RUN-CLIENT-PUBLISHED` | Publishing is the implementer's, not this repository's: it ships no publishing Job or schedule. A result's UC tags are the contract. An implementer's Job that publishes shared definitions writes them to the environment's `results`, from published artifacts only (`RUN-CLIENT-DRAFT-RESULTS`), and tags them, and other identities read them under their grants; only the identity that wrote a result reuses it (`RUN-CLIENT-RESULT-OWNERSHIP`). |
| `RUN-CLIENT-REFRESH` | Refresh a calculation in the same Delta table, retain only current completion, preserve pinned versions within retention. A calculation is not owner-scoped: its identity carries no owner (`RUN-CLIENT-RESULT-IDENTITY`). |
| `RUN-CLIENT-REFRESH-RESUME` | Explicit recompute saves one UTC computed-since cutoff; retries reuse matching defines completed since it. |
| `RUN-CLIENT-REQUEST-REOPEN` | Reconstruct the request, batch compile, discover UC completion tags; current-table reads use caller grants. |
| `RUN-CLIENT-RESULT-IDENTITY` | A result's identity, and the fingerprint digits in its table name, is one hash of: an identity format version, the define's emitted SQL, the parameter values it uses, the identities of the defines it reads, and the identities of the data and terminology resolvers bound, and the codes of the value sets used (`CLIENT-RESOLVER-IDENTITY`). The destination, the user, the compiler version, the library and define names and the population label are not part of it; the library and define appear only in the readable part of the table name (`RUN-CLIENT-TABLE-NAMES`). So one calculation by one identity has one table name in every schema, and its writer finds it by that name (`RUN-CLIENT-RESULT-OWNERSHIP`). Data freshness is the resolver's: it serves as-of data. A release that changes the runtime functions tells callers to refresh everything. A non-deterministic define is never reused (`DEC-RESULT-IDENTITY`). The hash is the `fingerprint`, and a result table carries four tags: `cql_result_format`, `cql_result_state`, `cql_computed_at` and `cql_fingerprint`. The compiler computes the fingerprint (`PIPE-DURABLE-IDENTITY`); a durable compile that retrieves data must bind a reusable data resolver. |
| `RUN-CLIENT-RESUME` | Extend calculations in the same session and resolve authorized results in new sessions without depending on Python objects or caches. |
| `RUN-CLIENT-REUSE` | Completion metadata and matching inputs govern reuse; skip if computed and recompute are the only modes. |
| `RUN-CLIENT-SESSION-REUSE` | A SQL warehouse session is held like a Spark session. Its holder keeps it open across statements, runs and measures and closes it when done, so the runtime functions' sandbox starts once per session, not once per run or statement. A script or Job holds one for its run; an App holds one per signed-in user per warehouse, and one for its service principal, for the life of its process, closed at shutdown. The session's statements share one connection, one call at a time; several define statements run side by side in it (`RUN-CLIENT-PARALLEL-DEFINES`), and a second concurrent run by the same user shares it with the first. Statements off the run's thread (the completion tag writes) use side connections and never wait on it. A held connection idle over 60 s is checked with `SELECT 1` before its next statement and replaced if its session ended; no statement is retried. Implemented in `WarehouseSession` and `cql_databricks_common.warehouse`. |
| `RUN-CLIENT-TABLE-NAMES` | Result tables are named `<library>_<define>_<first 16 fingerprint hex digits>_<first 8 hex digits of the SHA-256 of the writer's principal name>` (`RUN-CLIENT-RESULT-OWNERSHIP`): lowercase, digits and single underscores only. |
| `RUN-CLIENT-WAREHOUSE-CONNECTOR` | Every SQL statement the repository sends to a SQL warehouse goes through `WarehouseSession` (`databricks-sql-connector`); nothing calls the Statement Execution API. Rows come back typed, as Spark's do; ARRAY, STRUCT and MAP values come back as JSON text, and cloud fetch is off, so rows read the same with or without `pyarrow`. A statement still running after 30 minutes fails (`STATEMENT_TIMEOUT`). Every failure raises `ResolverError` with the statement's text, and the observer receives every statement the warehouse accepted, failed or not. |
| `RUN-CONTEXT-BY-ID` | Goal, not built: a request names its patient context as one or more subject IDs or the whole data source. |
| `RUN-EVAL-ID-INPUTS` | Result table columns include `__eval_id` marker and input fingerprints for debugging; future enhancements can populate data term columns. |
| `RUN-EVENTS` | The executor records each dataset attempt with run id, fingerprint, decision and timestamps. |
| `RUN-EXECUTOR` | Datasets start execution when their upstream dependencies have committed, not level-by-level, with per-warehouse concurrency caps and transient failure retries. |
| `RUN-JOB-OWNER` | Every run uses the service principal's identity; the requester check is performed on the durable calculation request before submission. |
| `RUN-JOB-RECOVERY` | A Job deleted out of band is recreated, not treated as a permanent error. |
| `RUN-LEASE` | Durable evaluation identity is independent of whether a lease table exists; correctness depends only on deterministic fingerprinting of inputs. |
| `RUN-NOTHING-TO-DO` | A run is skipped only when no barrier SQL changed and every result table exists and is current. |
| `RUN-ONE-WRITER` | One writer per evaluation; a new run cancels the old and waits for in-flight statements to finish. |
| `RUN-POINT-CALC` | Goal, not built: single-patient lookup returns precomputed answers; single-patient calculation runs the same SQL as batch with one patient. |
| `RUN-PROVENANCE` | Every result table is stamped with library, version, define, data source and period in tags and schema comments. |
| `RUN-READ-BARRIER` | A filtered retrieve becomes a materialized barrier with its own table, task and content-addressed name. |
| `RUN-RECOMPUTE-OVERRIDE` | Setting `recompute: true` on a run request forces full evaluation of the entire closure regardless of cache state. |
| `RUN-RESULTS-BY-EVAL` | Result table shape is standardized: one table per evaluation set, clustered by `(__eval_id, __subject_id)`, containing `__eval_id` marker for REPLACE semantics. |
| `RUN-RESULTS-WRITER` | Only the evaluator's service identity writes to the results store, and only through versioned evaluation logic. |
| `RUN-SCHEMA-NAME` | Result table names encode library and data source; multiple evaluations can write into one schema under distinct result tables addressed by evaluation ID. |
| `RUN-STANDING-COST` | Goal, not built: a standing request serves multiple subscribers from the evaluator's warehouse without charge splits. |

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
| `EMIT-SQL-BARRIER-TABLE` | Reverted — the emit barrier is `CACHE TABLE`, not a managed `CREATE TABLE`. | `data-format.md` |
| `EMIT-NOTEBOOK` | Retired — pretty-printed DataFrame-code notebook; moot when dataset body is `cql()` stub. | `pipeline-runtime.md` |
| `EMIT-CONFIG-HOIST` | Retired — hoisting repeated measurement-period literal out of DataFrame code; moot under runtime-compile. | `pipeline-runtime.md` |
| `BUILD-COMPILE-SPARK-URL` | Retired — compile service does not require reachable Spark (choice member schemas come from the static model). | `pipeline-runtime.md` |
| `PIPE-REFERENCE-RUNTIME` | Retired — Enzyme gets no incrementality through opaque UDF catalog; full-refresh orchestration does identical work without planner. | `pipeline-runtime.md` |
| `PIPE-RUNTIME-COMPILE` | Survives as documented principle; SDP runtime retired. `cql()` runtime-compiles to Spark SQL. | `pipeline-runtime.md` |
| `PIPE-SDP` | Retired — SDP and Lakeflow pipeline orchestration are removed. | `pipeline-runtime.md` |
| `PIPE-PACKAGE` | Retired with SDP runtime. | `pipeline-runtime.md` |
| `PIPE-APPLY` | Retired with SDP runtime. | `pipeline-runtime.md` |
| `BIND-HINT-ENVELOPE` | Retired — `fn(context VARIANT, hints VARIANT)` envelope pair. Replaced by `BIND-EXPLICIT-PARAMS`. | `data-binding.md` |
| `BIND-FEDERATE` | Retired — federation inside client SQL UDTF body, folded by literal arguments. | `data-binding.md` |
| `BIND-SOURCE-CATALOG` | Retired — no `TBLPROPERTIES` catalog interrogation; binding table passed over compile API. | `data-binding.md` |
| `BIND-SOURCE-PRUNE` | Retired — source pruning is Catalyst arm-elimination inside UDTF. | `data-binding.md` |
| `BIND-MAP` | Retired — most-specific-source selection subsumed by ordinary SQL in UDTF body. | `data-binding.md` |
| `BIND-SOURCE-DISCRIMINATOR` | Retired — engine-enforced cohort filter. Conformance defines cohort by isolated fixture data. | `data-binding.md` |
| `BIND-RETRIEVE-SUBJECT` | Retired — the optional `retrieve_<Type>_ids(subject_ids, <the eight>)` variant and the subject-scoped compile that called it. No caller scoped a compile to patients. Earlier deploys' `_ids` functions are dropped by provisioning. | `data-binding.md` |
| `CONF-GATE-SELFDIFF` | Retired as dedicated gate; enforced transitively by `CONF-GATE-MEASURE`. | `testing-and-conformance.md` |
| `EMIT-SQL-CHOICE-INTERVAL` | Evaluated and rejected — folding choice dispatch into coarse UDF increased emitted SQL even as CASE counts fell. | `execution-model.md` |
| `EVAL-EVAL-TREE-SHARED` | Applies only to fallback `EVAL-DEFINE-EVALUATOR`; the ELM tree built once per executor JVM. | `execution-model.md` |
| `EXEC-SQL-TEMPORAL-RUNTIME-UNIT` | Known gap on native `make_interval` path (runtime unit dispatch costs 14–17x plan size). Honored on opaque-UDF path. | `execution-model.md` |
| `PIPE-READ-DATASET` | A materialized retrieve read published as its own `DatasetRecipe`; name CONTENT-ADDRESSED, `read_<type>[_<filter>]_<hash8>`. | `pipeline-runtime.md` |
| `PHASE-0` | Old roadmap phase. Retired: old roadmap phase. | `../../docs/roadmap.md` |
| `PHASE-1` | Old roadmap phase. Retired: old roadmap phase. | `../../docs/roadmap.md` |
| `PHASE-2` | Old roadmap phase. Retired: old roadmap phase. | `../../docs/roadmap.md` |
| `PHASE-3` | Old roadmap phase. Retired: old roadmap phase. | `../../docs/roadmap.md` |
| `PHASE-4` | Old roadmap phase. Retired: old roadmap phase. | `../../docs/roadmap.md` |
| `PHASE-5` | Old roadmap phase. Retired: old roadmap phase. | `../../docs/roadmap.md` |
| `PHASE-6` | Old roadmap phase. Retired: old roadmap phase. | `../../docs/roadmap.md` |
| `PHASE-7` | Old roadmap phase. Retired: old roadmap phase. | `../../docs/roadmap.md` |
| `PHASE-8` | Old roadmap phase. Retired: old roadmap phase. | `../../docs/roadmap.md` |
| `PHASE-9` | Old roadmap phase. Retired: old roadmap phase. | `../../docs/roadmap.md` |
| `PHASE-10` | Old roadmap phase. Retired: old roadmap phase. | `../../docs/roadmap.md` |
| `RUN-CLIENT-BATCH-METADATA` | Retired: bulk discovery remains. See `RUN-CLIENT-CATALOG-CHECKPOINTS`. |  |
| `RUN-CLIENT-OPERATIONS` | Retired: publishing and scheduling are the implementer's. See `RUN-CLIENT-PUBLISHED`. |  |
| `RUN-CLIENT-PORTABLE-HANDLE` | Retired: request reopening does not use compiled-plan handles. See `RUN-CLIENT-REQUEST-REOPEN`. |  |
| `SVC-COMPILER-UC` | Retired with SVC-COMPILER-M2M. |  |
