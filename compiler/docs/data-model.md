# Data model

How the compiler represents the FHIR R4 data model: a static, typed Spark schema derived at build time.
The compiler never asks Spark for a schema while it compiles.

Requirements here: `DATA-MODEL-SEAM`, `DATA-MODEL-SCHEMA`, `DATA-MODEL-CUTS`, `DATA-PATH-IN-SCHEMA`,
`DATA-MODEL-STATIC-STRUCT`, `DATA-NO-EMPTY-STRUCT`, `TYPE-FHIR-MODEL`, `BUILD-FHIR-CODEGEN`, `DATA-SCHEMA-CONTRACT`,
`TYPE-OFFLINE-COMPILE`, `ARCH-RESOURCE-HANDLE`, `ARCH-RETRIEVE-PROJECT`. They implement
`ARCH-NO-RUNTIME-SCHEMA`.

## 1. A generated model, read as a schema

An ELM `using` names a data model. The compiler resolves types and physical schema from a model generated
at build time from the model's published definitions.

The generated artifact, `sema.generated.FhirR4Model`, is plain data: resources, their elements, and each
element's type, cardinality and choice slots. It has no Spark or FHIR library dependency. Hand-written code
maps it to the `CqlType` lattice (`sema/FhirModel.scala`), to the shape the data model keeps
(`sema/FhirShape.scala`, also Spark-free) and to a Spark `StructType` (`sema/FhirSchema.scala`).

The compiler has no runtime FHIR (HAPI) dependency. HAPI is used only on the test and reference side
(`compiler/cqf`, `compiler/conformance`).

## 2. The seam — `DATA-MODEL-SEAM`

FHIR-specific facts sit behind a seam (`ARCH-SEAMS`). For a type and an element path it answers: the
element's type, its cardinality, whether it is a choice and which slots it has, and the physical schema
of a resource. Typing and lowering use only the seam and the `CqlType` lattice.

FHIR R4 is the only implementation. `FhirModel` and `FhirSchema` become a `DataModel` trait only when a
second model arrives (`ARCH-DATAMODEL-TRAIT`).

## 3. Source: the FHIR R4 XSDs — `BUILD-FHIR-CODEGEN`

- Input: the FHIR R4 (4.0.1) XSDs, committed under `compiler/core/model-schemas/fhir-r4/` with their
  provenance and checksum in its `README.md`. Codegen never downloads.
- Task: `generateFhirModel` runs `codegen.FhirModelGenerator`, which writes Scala to
  `compiler/core/build/generated-sources/fhir-model/`. `compileScala` depends on it. The generated code is
  not committed.
- Output is deterministic (`TECH-DETERMINISM`).
- Scope: an allow-list of resources in `FhirModelGenerator.AllowedResources`, plus every datatype they
  reach. Adding a resource is a one-line change there.
- `FhirModel.resourceTypes` lists the modeled resources, and only resources (`SVC-RESOURCE-SCHEMA`). The
  compile service's `/resource-schema` serves it.

XSDs carry no profile constraints (slicing, fixed codes, extension definitions). Retrieves resolve
profiles to their base resource, so the base schema is enough for navigation and `value[x]`. Profiled
extensions, such as us-core-sex for `Patient.sex`, are modeled by hand (`FhirModel.profiledExtensionValueType`).

## 4. Schema conventions — `DATA-MODEL-SCHEMA`

The schema is FHIR R4 JSON with some fields left out. Nothing is moved or renamed, so any FHIR JSON reads into
it, and a field it leaves out is dropped on read. There is no depth cap: every element of every modeled type is
present at any depth, except where a rule below leaves it out.

| FHIR shape | Spark encoding | Notes |
|---|---|---|
| primitive | JSON-native scalar | `boolean` → `boolean`; `integer`, `positiveInt`, `unsignedInt` → `long`; `decimal` → `double`; string-backed types (`string`, `code`, `uri`, `date`, `dateTime`, `instant`, `time` and others) → `string`. Values convert at use (`ToDate` to a temporal struct, a quantity value to `decimal(28,8)`) |
| `0..*` | `array<element>` | Otherwise the bare element type. Code membership reads this to decide single or list (`DATA-SCHEMA-CONTRACT`) |
| `value[x]` | one typed slot per type | `valueQuantity`, `valueString`, `valueCodeableConcept`, as in FHIR JSON. Navigation picks the slot by the declared type. There is no merged `value` field |
| a type loop | cut at a fixed edge | `DATA-MODEL-CUTS`, below |
| `extension` | on resources only | An Extension is `url`, one slot per open value type, and `extension`, one nested level whose entries have no `extension`. Extensions on other elements, and on primitives (`_birthDate`), are left out |
| `modifierExtension` | on resources and backbone elements | On a resource it has the nested Extension shape. On a backbone element it keeps `url` only, so a reader can see that one is present |
| `contained` | left out | FHIR forbids nesting contained resources, so it is not a loop; it would add every resource type's width |

The open value types are `boolean`, `integer`, `decimal`, `string`, `code`, `uri`, `url`, `canonical`,
`dateTime`, `date`, `time`, `instant`, `Coding`, `CodeableConcept`, `Quantity`, `Period`, `Reference` and
`Identifier` (`FhirShape.OpenValueTypes`). Each slot is a full datatype, so all 50 of FHIR's would make one
extension wider than a resource. Adding a type is a one-line change.

`FhirShape` holds these rules. `FhirSchema` renders its shape as Spark types, and the compile service serves
the result from `/resource-schema` (`SVC-RESOURCE-SCHEMA`).

### 4.1 Loop cuts — `DATA-MODEL-CUTS`

With extensions and `contained` set aside, the FHIR R4 types our resources reach have one loop:
`Reference.identifier → Identifier.assigner → Reference`. It is cut at `Identifier.assigner`: there a Reference
is `{reference, type, display}`, with no `identifier`. A Reference keeps its identifier everywhere else. An
Extension's `extension` is the second cut: a nested extension has no `extension`.

Every other datatype has exactly one shape, whatever its depth. The cuts are a committed list,
`FhirShape.Cuts`. Building a shape fails on a loop that is not in the list, naming its edge, so a resource whose
model brings a new loop (`Questionnaire.item.item`, for one) needs an entry before it can be modeled.

### 4.2 Paths outside the data model — `DATA-PATH-IN-SCHEMA`

Navigating to an element FHIR R4 defines but the data model leaves out fails the compile, naming the rule:
`Identifier.assigner.identifier`, an extension on an element or a primitive, an extension nested two levels,
a value of a backbone `modifierExtension`, or `contained`. It never reads as null or fails when the SQL runs.

`passes.SchemaPaths` follows each navigation from where it starts (a retrieve, a query alias, a `let` or a
define) through the shape, so it sees a cut where the cut lands. It runs on every compile, before lowering. An
element FHIR does not define, such as a profile element or a primitive's `.value`, is left to lowering.

### 4.3 No empty structs — `DATA-NO-EMPTY-STRUCT`

A complex type that keeps no fields is omitted instead of written as an empty `STRUCT<>`
(`FhirShape.shapeOf`). Table formats reject an empty struct column.

### 4.4 A static struct — `DATA-MODEL-STATIC-STRUCT`

Resources are typed structs, not `VARIANT` or JSON strings. CQL navigation reads a few fields from each
resource, and a struct stores each field as its own column, so a query reads only those columns. The compiler
types every path from the model either way, and a struct lets the reshape check refuse a wrong type at publish
instead of a measure meeting it at run time. [docs/decisions/resource-schema.md](../../docs/decisions/resource-schema.md)
records the comparison.

`VARIANT` appears in one place: the retrieve function's `parameters MAP<STRING, VARIANT>` argument
The heterogeneous-choice `VARIANT` encoding is not used; the flat carrier
(`EMIT-SQL-FLAT-CHOICE`) replaces it.

### 4.5 Demand-pruned resources — `ARCH-RETRIEVE-PROJECT`, `ARCH-RESOURCE-HANDLE`

A resource carried through the plan is wide at analysis time, before Catalyst can prune columns. Two
mechanisms narrow it. Both use the static model, never a runtime schema read.

- Ordinary retrieves (`ARCH-RETRIEVE-PROJECT`): `passes.RetrieveDemand` collects the top-level fields the
  library navigates, expands `value[x]` bases to their slots, and keeps the identity fields and the
  scan-filter properties. The retrieve's `__resource` is projected to that set. A type falls back to the
  full struct when a whole resource escapes the analysis, or when it takes part in a heterogeneous choice.
  `lower.retrieve_views` controls whether retrieves are emitted as their own views.
- Heterogeneous choices (`ARCH-RESOURCE-HANDLE`, `EMIT-SQL-FLAT-CHOICE`): `[A] union [B]` carries a
  handle, not every arm's full body. The handle is the identity columns (`__choice`, `__resource_type`,
  `__resource_id`, `__subject_id`, `__ordinal`) plus the fields consumers navigate (`passes.ChoiceDemand`),
  as top-level slots.

## 5. Two views of the model

- `CqlType`, the semantic type: `Patient.birthDate` is a `Date`. It drives operator dispatch.
- The physical schema: `Patient.birthDate` is a `string` (ISO). It becomes a temporal struct at use.

`typeOf` carries the `CqlType`. `FhirSchema` carries the physical schema.

## 6. Types at FHIR leaves — `TYPE-FHIR-MODEL`

The model makes `typeOf` precise at FHIR leaves. A `Retrieve` has the resource's structured type, and
navigation into modeled elements types from the model. Unmodeled navigation stays `AnyType`. `typeOf` is
still a recomputed attribute (`ARCH-TYPE-ATTR`).

## 7. Schema contract — `DATA-SCHEMA-CONTRACT`, `DATA-CONTEXT-RESOURCE`

Lowering reads every schema it needs from the model: code cardinality for membership, resource field
names, query alias schemas and the context frame. It never reads a live frame.

The resource struct is schema-complete: every modeled element is present, typed null when absent from the
data, so navigation is total. The context base frame carries the context resource as a column, so a
measure can read `Patient.birthDate` directly.

## 8. Offline compile — `TYPE-OFFLINE-COMPILE`

Typing and lowering make no Spark call, for every library. The SQL back end has no `SparkSession` to call.
`OfflineCompileSpec` lowers multi-library measures and the heterogeneous-choice fixtures, with no
session in scope and checks the rendered script.
