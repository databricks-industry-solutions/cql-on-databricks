# Data model

How the compiler represents the FHIR R4 data model: a static, typed Spark schema derived at build time.
The compiler never asks Spark for a schema while it compiles.

Requirements here: `DATA-MODEL-SEAM`, `DATA-MODEL-SCHEMA`, `DATA-MODEL-STATIC-STRUCT`,
`DATA-NO-EMPTY-STRUCT`, `TYPE-FHIR-MODEL`, `BUILD-FHIR-CODEGEN`, `DATA-SCHEMA-CONTRACT`,
`TYPE-OFFLINE-COMPILE`, `ARCH-RESOURCE-HANDLE`, `ARCH-RETRIEVE-PROJECT`. They implement
`ARCH-NO-RUNTIME-SCHEMA`.

## 1. A generated model, read as a schema

An ELM `using` names a data model. The compiler resolves types and physical schema from a model generated
at build time from the model's published definitions.

The generated artifact, `sema.generated.FhirR4Model`, is plain data: resources, their elements, and each
element's type, cardinality and choice slots. It has no Spark or FHIR library dependency. Hand-written code
maps it to the `CqlType` lattice (`sema/FhirModel.scala`) and to a Spark `StructType`
(`sema/FhirSchema.scala`).

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

| FHIR shape | Spark encoding | Notes |
|---|---|---|
| primitive | JSON-native scalar | `boolean` → `boolean`; `integer`, `positiveInt`, `unsignedInt` → `long`; `decimal` → `double`; string-backed types (`string`, `code`, `uri`, `date`, `dateTime`, `instant`, `time` and others) → `string`. Values convert at use (`ToDate` to a temporal struct, a quantity value to `decimal(28,8)`) |
| `0..*` | `array<element>` | Otherwise the bare element type. Code membership reads this to decide single or list (`DATA-SCHEMA-CONTRACT`) |
| `value[x]` | one typed slot per type | `valueQuantity`, `valueString`, `valueCodeableConcept`, as in FHIR JSON. Navigation picks the slot by the declared type. There is no merged `value` field |
| extensions | resource level only | `extension` is a bounded struct: `url` plus one slot per open value type. It is dropped from nested structs. `contained` and `modifierExtension` are dropped |
| nesting | capped at `data.fhir_max_nesting_level` (default 5) | Bounds recursion through self-referential types. Elements past the cap are dropped; navigating past the cap gives null |

### 4.1 No empty structs — `DATA-NO-EMPTY-STRUCT`

A complex type whose children are all dropped at the cap would leave an empty `STRUCT<>`. It is omitted
instead (`FhirSchema.nestedStruct`). Navigating into it would give null anyway, and table formats reject
an empty struct column.

### 4.2 A static struct — `DATA-MODEL-STATIC-STRUCT`

Resources are typed structs, not `VARIANT` or JSON strings. CQL navigation reads a few fields from each
resource, which suits typed columns and column pruning. The nesting cap limits the dynamic tail (open
extensions, self-referential chains); it is a coverage limit, not a speed trade.

`VARIANT` appears in one place: the retrieve function's `parameters MAP<STRING, VARIANT>` argument
(`BIND-EXPLICIT-PARAMS`). The heterogeneous-choice `VARIANT` encoding is retired
(`EMIT-SQL-VARIANT-CHOICE`).

### 4.3 Demand-pruned resources — `ARCH-RETRIEVE-PROJECT`, `ARCH-RESOURCE-HANDLE`

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
