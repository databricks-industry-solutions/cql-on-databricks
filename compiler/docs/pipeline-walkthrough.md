# Pipeline walkthrough

Two small CQL programs traced through the passes in [engine-design.md](engine-design.md) section 2, from
CQL to emitted Spark SQL. The SQL below is the output of the local compile host
(`./gradlew :compiler:service:runCompileHost`) for `POST /compile-sql`.

Only two steps cross a representation: Load (ELM to IR) and Lower (IR to SQL). The rest are IR to IR.

| Step | In → out | Code |
|---|---|---|
| Translate | CQL → ELM JSON | [`CqlToElmCli`](../core/src/main/scala/com/databricks/cql/translate/CqlToElmCli.scala) |
| Load | ELM JSON → IR | [`ElmCodec`](../core/src/main/scala/com/databricks/cql/load/ElmCodec.scala) |
| Resolve | IR → IR | [`Pipeline.resolve`](../core/src/main/scala/com/databricks/cql/passes/Pipeline.scala), [`DefineGraph`](../core/src/main/scala/com/databricks/cql/verify/Verifier.scala) |
| Normalize | IR → IR | [`FunctionInliner`](../core/src/main/scala/com/databricks/cql/passes/FunctionInliner.scala), [`ParameterResolver`](../core/src/main/scala/com/databricks/cql/passes/ParameterResolver.scala) and others in `Pipeline.runNormalize` |
| Optimize | IR → IR | [`Optimize`](../core/src/main/scala/com/databricks/cql/passes/Optimize.scala) |
| Lower | IR → Spark SQL | [`SqlFrameLowering`](../core/src/main/scala/com/databricks/cql/transpiler/lower/SqlFrameLowering.scala), [`SqlColumnLowering`](../core/src/main/scala/com/databricks/cql/transpiler/lower/SqlColumnLowering.scala), [`SqlQueryLowering`](../core/src/main/scala/com/databricks/cql/transpiler/lower/SqlQueryLowering.scala) |
| Schedule | order the define graph | [`Schedule`](../core/src/main/scala/com/databricks/cql/passes/Schedule.scala) |

The IR is the case classes in [`Ir.scala`](../core/src/main/scala/com/databricks/cql/ir/Ir.scala). Load
drops the non-semantic ELM annotations (`localId`, `locator`, `annotation`, `resultType*`), so they are
left out below. [`SqlRender`](../core/src/main/scala/com/databricks/cql/transpiler/emit/SqlRender.scala)
renders the SQL. No `SparkSession` exists at compile time (`EXEC-SQL-BACKEND`).

## Example A: a function and a parameter

Normalize does the work here. A function is inlined and a parameter bound to its default, so Lower sees
plain arithmetic. There is no `context`, so the context is `Unfiltered`: one synthetic subject.

### CQL

```cql
library Demo version '1.0.0'

parameter "Threshold" Integer default 3

define function "Double"(n Integer): n * 2

define "Score": "Double"(2) + "Threshold"
```

### ELM (after Translate)

The cqframework front end resolves the overload. `"Double"(2)` is a `FunctionRef` to one target.

```jsonc
{ "library": {
  "identifier": { "id": "Demo", "version": "1.0.0" },
  "parameters": { "def": [
    { "name": "Threshold", "parameterTypeSpecifier": { "name": "Integer" },
      "default": { "type": "Literal", "valueType": "{urn:hl7-org:elm-types:r1}Integer", "value": "3" } } ] },
  "statements": { "def": [
    { "type": "FunctionDef", "name": "Double",
      "operand": [ { "name": "n", "operandTypeSpecifier": { "name": "Integer" } } ],
      "expression": { "type": "Multiply", "operand": [
        { "type": "OperandRef", "name": "n" },
        { "type": "Literal", "valueType": "...Integer", "value": "2" } ] } },
    { "type": "ExpressionDef", "name": "Score",
      "expression": { "type": "Add", "operand": [
        { "type": "FunctionRef", "name": "Double", "operand": [
          { "type": "Literal", "valueType": "...Integer", "value": "2" } ] },
        { "type": "ParameterRef", "name": "Threshold" } ] } } ] } } }
```

### IR (after Load)

One case class per node, shown with empty `Library` fields left out and type names shortened. `Double`
is still a `FunctionDef`. The `WellFormedLibrary` verifier runs here.

```scala
Library(id = "Demo", version = Some("1.0.0"),
  parameters = Seq(ParameterDef("Threshold", parameterTypeName = Some("Integer"), default = Some(Literal("Integer", Some("3"))))),
  statements = Seq(
    FunctionDef("Double", context = None,
      operands = Seq(OperandDef("n", Some("Integer"))),
      body = Multiply(OperandRef("n"), Literal("Integer", Some("2"))), fluent = false, external = false),
    ExpressionDef("Score", context = None,
      expression = Add(
        FunctionRef("Double", libraryName = None, operands = Seq(Literal("Integer", Some("2")))),
        ParameterRef("Threshold", libraryName = None)))))
```

### Resolve

Resolve builds the define graph and rejects cycles (`LANG-FUNCTION`). `Score` references no other define,
so the tree is unchanged.

### Normalize

Functions are inlined first, so parameters inside inlined bodies are bound too.

1. `FunctionInliner` replaces `Double(2)` with its body, `n` bound to `2`, and drops the `FunctionDef`.
2. `ParameterResolver` replaces `ParameterRef("Threshold")` with its default (`LANG-PARAMETER`).

```scala
ExpressionDef("Score", None,
  Add(Multiply(Literal("Integer", Some("2")), Literal("Integer", Some("2"))),
      Literal("Integer", Some("3"))))
```

The `OnlyKernelNodes` verifier runs after Normalize: no `FunctionRef`, `OperandRef` or `ParameterRef` may
remain.

### Lower

The `Unfiltered` context becomes a one-row base frame with subject `'*'`. Each define is a view over it,
with its value in `__val` (`EMIT-SQL-VALUE-COLUMN`). `Score` is a cheap scalar, so it is a view, not a
barrier ([data-format.md](data-format.md) section 6).

```sql
CREATE OR REPLACE TEMP VIEW __frame_base AS
SELECT
  '*' AS __subject_id
FROM (
  SELECT 1
) AS __one

CREATE OR REPLACE TEMP VIEW Score AS
SELECT
  __subject_id,
  ((2 * 2) + 3) AS __val
FROM __frame_base
```

| `__subject_id` | `__val` |
|---|---|
| `*` | `7` |

### A lowering rule encodes CQL semantics

Lower is not a transcription. Each rule applies CQL semantics where they differ from SQL's. CQL `22 / 7`
converts both operands to Decimal, returns null on division by zero (`LANG-RUNTIME-COND`) and keeps 8
decimal places:

```sql
CASE WHEN (CAST(7 AS DECIMAL(28,8)) = 0) THEN NULL
     ELSE round((CAST(22 AS DECIMAL(28,8)) / CAST(7 AS DECIMAL(28,8))), 8) END AS __val
```

See the `Divide` case in `SqlColumnLowering.scala`. A repeated pure-scalar operator instead lowers to an
opaque UDF (`EXEC-UDF-FIRST`, [execution-model.md](execution-model.md)).

## Example B: a FHIR retrieve

One expression evaluated across every patient at once, keyed by `__subject_id` (`ARCH-SET-EVAL`).
`FhirRetrieveSpec` runs this shape against sample data.

### CQL

```cql
library Demo version '1.0.0'
using FHIR version '4.0.1'
context Patient
define "HasAnyCondition": exists [Condition]
```

### ELM and IR

```jsonc
{ "type": "ExpressionDef", "name": "HasAnyCondition", "context": "Patient",
  "expression": { "type": "Exists", "operand": {
    "type": "Retrieve", "dataType": "{http://hl7.org/fhir}Condition" } } }
```

```scala
ExpressionDef("HasAnyCondition", context = Some("Patient"),
  expression = Exists(Retrieve(
    dataType = "{http://hl7.org/fhir}Condition",
    templateId = None, codeProperty = None, codes = None, dateProperty = None, dateRange = None)))
```

Resolve, Normalize and Schedule change nothing: no references, functions or parameters, and one define.

### Lower

The Patient context becomes a base frame over the relation bound to the `[Patient]` call site. Each retrieve
reads `__subject_id` and `__resource` from its bound relation ([data-binding.md](data-binding.md)). The
`<… relation>` placeholders below stand for the table each call site is bound to. `exists` is a left join to
the subjects that have a Condition row, with absent subjects coalesced to `false`.

```sql
CREATE OR REPLACE TEMP VIEW __frame_base AS
SELECT
  __subject_id,
  __resource AS Patient
FROM (SELECT __subject_id, __resource FROM <Patient relation>)

CREATE OR REPLACE TEMP VIEW HasAnyCondition AS
SELECT
  __subject_id,
  coalesce(__exists_HasAnyCondition_0, false) AS __val
FROM (
  SELECT * FROM __frame_base LEFT OUTER JOIN (
    SELECT
      __subject_id,
      true AS __exists_HasAnyCondition_0
    FROM (
      SELECT DISTINCT
        __subject_id
      FROM (SELECT __subject_id, __resource FROM <Condition relation>) AS __sp
    ) AS __pr
  ) AS __j0 USING (__subject_id)
) AS __h
```

### Value-set membership

`exists [Condition: "Diabetes"]` differs only in how the subjects with a match are found. The retrieve
request carries the value set as `value_set`, and the lowering adds a semi-join of each
Condition's codings against the value-set table, filtered to that set
(`SqlQueryLowering.membershipSemiJoin`, `LANG-TERMINOLOGY`). The local compile host has no terminology, so
it rejects this example; the Compiler Service App compiles it.

## What each step guarantees

The verifiers (`CONF-VERIFIER`) turn the flow into checked invariants, so a pass bug surfaces at the pass
that caused it.

| After | Invariant | Check |
|---|---|---|
| Load | Every ELM node is lifted. An unmodeled node becomes an explicit unknown, never dropped (`CORE-NO-SILENT-MISLOWER`) | `WellFormedLibrary` |
| Resolve | No reference cycles | `DefineGraph.assertAcyclic` |
| Normalize | Only kernel nodes remain, and they type-check | `OnlyKernelNodes`, `WellTyped` |
| Lower | Every node emits SQL, or compilation fails with `UnsupportedNode` | the lowering is total by rejection |
