# Data model

How the compiler represents the FHIR R4 data model: a static, typed Spark schema derived at build time.
The compiler never asks Spark for a schema while it compiles.

Requirements here: `DATA-MODEL-SEAM`, `DATA-MODEL-SUBSET`, `DATA-MODEL-SCHEMA`, `DATA-MODEL-CUTS`, `DATA-PATH-IN-SCHEMA`,
`DATA-MODEL-STATIC-STRUCT`, `DATA-NO-EMPTY-STRUCT`, `TYPE-FHIR-MODEL`, `BUILD-FHIR-CODEGEN`, `DATA-SCHEMA-CONTRACT`,
`TYPE-OFFLINE-COMPILE`, `ARCH-RESOURCE-HANDLE`, `ARCH-RETRIEVE-PROJECT`. They implement
`ARCH-NO-RUNTIME-SCHEMA`.

## 1. A generated model, read as a schema

An ELM `using` names a data model. The compiler resolves types and physical schema from a model generated
at build time from the model's published definitions.

The generated artifact, `sema.generated.FhirR4Model`, is plain data: resources, their elements, and each
element's type, cardinality and choice slots. It has no Spark or FHIR library dependency. Hand-written code
maps it to the `CqlType` lattice (`sema/FhirModel.scala`), checks a bound relation's shape against it
(`sema/ResourceShape.scala`), builds the stock shape (`sema/FhirShape.scala`; all three Spark-free) and renders the
stock shape as a Spark `StructType` (`sema/FhirSchema.scala`).

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

## 4. The superset and the bound shape — `DATA-MODEL-SUBSET`

The compiler checks and types CQL against a superset: FHIR R4 for the modeled resources, as the generated model
holds it. That is every element, every `value[x]` slot, and `extension` and `modifierExtension` on every complex
element with all of FHIR's extension value types, at any depth. The type graph loops (`Reference → Identifier →
Reference`, an `Extension` inside an `Extension`), and needs no cuts: a shape is a finite struct. Two things FHIR
defines are outside the superset: `contained`, and extensions on primitive values (`_birthDate`).

Each relation a compile binds returns a subset of it, its shape: its `resource` struct (`BIND-CONTRACT`), with any
of the superset's fields, at any depth, in any order, and optionally the root columns `patient_id`
(`DATA-SUBJECT-KEY`), `min_date` and `max_date` (`BIND-PREFILTER`). The names, the types (the encodings in the table
below), and lists as lists are FHIR's. `id` is required. The caller reads each bound type's shape and sends it in
the request's `data.shapes` (`BIND-CALL-SITE`): the client from `information_schema` for an environment's views and
for a table bound per site, and the model's stock shape for a volume or DataFrame it reshapes. A client
session reads an environment's shapes once (`ResolverBinder.forget_shapes` reads them again). The compiler
reads no catalog. `sema.ResourceShape` checks each shape when the request is read and names every problem at once:
a field FHIR does not define there, a list that should be single or the reverse, a wrong primitive type, a struct
where FHIR has a primitive, `contained`, a primitive extension, a missing `id`. A retrieved type with no shape
fails the compile.

The shape decides two things:

- Which paths compile (`DATA-PATH-IN-SCHEMA`, 4.2).
- Which `value[x]` slots lowering reads. A path is a requirement the shape must meet; the slot a value is in is
  not, since CQL tests the types a choice may hold (FHIRHelpers tests every one). A slot the shape leaves out
  holds no value on any row, so `value is null`, a type test, a cast and a choice carrier read only the slots
  the shape holds (`sema.ViewSlots`), and the SQL names no field the relation does not return. `value is Ratio`
  on a view with no `valueRatio` is false. Lowering knows a value's FHIR type where it dispatches, not its path, so a nested type's slots are
  those its values carry anywhere in the request's shapes; on a resource they are exact.

A whole resource (`Full` demand) is every FHIR column the relation returns. A kept result's fingerprint covers the
shape of each type its compile retrieves (`CLIENT-RESOLVER-IDENTITY`). With no data bound (`describe`), paths are
checked against the superset alone and every slot is read.

## 4a. The stock shape — `DATA-MODEL-SCHEMA`

The stock shape is the subset the reshape, the loader and the store write, and the one `/resource-schema`
serves. It is FHIR R4 JSON with some fields left out. Nothing is moved or renamed, so any FHIR JSON reads into
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
the result from `/resource-schema` (`SVC-RESOURCE-SCHEMA`). These rules bound the stock shape only; a view may
return any subset of the superset (section 4).

### 4.1 Loop cuts — `DATA-MODEL-CUTS`

With extensions and `contained` set aside, the FHIR R4 types our resources reach have one loop:
`Reference.identifier → Identifier.assigner → Reference`. It is cut at `Identifier.assigner`: there a Reference
is `{reference, type, display}`, with no `identifier`. A Reference keeps its identifier everywhere else. An
Extension's `extension` is the second cut: a nested extension has no `extension`.

Every other datatype has exactly one shape in the stock shape, whatever its depth. The cuts are a committed
list, `FhirShape.Cuts`. Building the stock shape fails on a loop that is not in the list, naming its edge, so a
resource whose model brings a new loop (`Questionnaire.item.item`, for one) needs an entry before it can be
modeled. The cuts are not compile rules: a view that returns `Identifier.assigner.identifier` compiles CQL that
reads it.

### 4.2 Paths outside the bound data — `DATA-PATH-IN-SCHEMA`

Navigating to an element FHIR R4 defines but the bound shape leaves out fails the compile, naming the path:
"`Condition.clinicalStatus` is not in this environment". So does a `value[x]` choice none of whose slots the
shape holds. `contained` and an extension on a primitive fail as not supported by the compiler. None of them
reads as null or fails when the SQL runs. The fields a retrieve's own filters read count too: its code, its
date, a period pre-filter and, for a profile, `meta.profile`.

`passes.SchemaPaths` follows each navigation from where it starts (a retrieve, a query alias, a `let` or a
define) through the bound shape, so it sees what the data holds where the navigation lands. It runs on every
compile, before lowering. Where a start does not trace to a retrieve, and when no data is bound, it checks
against the superset alone. An element FHIR does not define, such as a profile element or a primitive's
`.value`, is left to lowering.

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
  Every retrieve reads the relation its call site binds (`BIND-CALL-SITE`).
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

The resource struct holds what its shape holds (`DATA-MODEL-SUBSET`), each field typed null when absent from a
row, and the path check has refused every navigation the shape does not hold, so navigation is total. The
context base frame carries the context resource as a column, so a measure can read `Patient.birthDate`
directly.

## 8. Offline compile — `TYPE-OFFLINE-COMPILE`

Typing and lowering make no Spark call, for every library. The SQL back end has no `SparkSession` to call.
`OfflineCompileSpec` lowers multi-library measures and the heterogeneous-choice fixtures, with no
session in scope and checks the rendered script.
