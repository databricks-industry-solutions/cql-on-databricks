# The resource schema

Status: settled (`DEC-RESOURCE-VARIANT`). The requirements it settles are `DATA-MODEL-STATIC-STRUCT`,
`DATA-MODEL-SCHEMA` and `DATA-MODEL-CUTS`, in the [compiler registry](../../compiler/docs/requirements.md).
How the schema is built is in [data-model.md](../../compiler/docs/data-model.md). The schema below is the stock
shape, the one the reshape and the store write. The compiler no longer bounds CQL by it: a view may return any
subset of FHIR R4, and the compile checks paths against the shape the view returns (`DATA-MODEL-SUBSET`,
[verified data mapping](verified-data-mapping.md)).

## Problem

A retrieve reads `__resource`, a typed struct generated from FHIR R4. The struct was bounded by a depth cap of
five levels: every complex element below it was dropped. The cap had to rise twice for paths measures read
(a discharge disposition's coding, a dose's bounds period), and it gave one datatype a different shape at
different depths, so a CodeableConcept had its `coding` in one place and not in another. Reading past the cap
failed when the SQL ran, not when it compiled. The cap was also a request setting, while a published view has
one fixed shape, so the two could disagree.

## Options

| Option | For | Against |
|---|---|---|
| `VARIANT` for every resource | Any FHIR JSON loads with `parse_json`; no cap; nothing is dropped | Unshredded, reading one field reads the whole resource. The compiler still types every path from the model. A wrong type surfaces while a measure runs instead of at publish |
| A struct plus `VARIANT` at the cuts | Nothing is dropped | Two encodings: two read paths in lowering, two reshape paths, two error stories and two conformance surfaces |
| A flat model of our own, with SQL on FHIR ViewDefinitions from FHIR | Easy to fill from data that is not FHIR | CQL walks lists inside a row, so every list read becomes a join: most of lowering is rewritten. It holds only the paths chosen, so a measure reading another needs a new model version every customer re-maps |
| A struct, a subset of FHIR R4 JSON, cut at loops | Any FHIR JSON reads into it; one shape per type; no depth number | A few things stay out (below) |

A local comparison on one million synthetic Observations, reading status, code, date, subject and value:
the struct table was 30.8 MB and the query read 9.8 MB of it; the unshredded `VARIANT` table was 70.4 MB and
the query read all of it. The best of three runs took 0.18 s on the struct and 0.32 s on the `VARIANT`.
Shredded on exactly the fields read, with scan pushdown, the `VARIANT` query took 0.29 s, and its plan read
struct fields. At its best a `VARIANT` becomes a struct picked from the data rather than the model. These were
local runs on OSS Spark 4.0.1, without Photon or cloud storage.

## Decision

`__resource` stays a typed struct. The schema is FHIR R4 JSON with some fields left out, never moved or
renamed, so any FHIR JSON reads into it. Every element is present at any depth, except:

- **Loop cuts.** With extensions and `contained` set aside, the types our resources reach have one loop,
  `Reference → Identifier → Reference`. It is cut at `Identifier.assigner`, where a Reference has no
  `identifier`. A nested extension has no `extension`. The cuts are a committed list, and a loop not in it fails
  the schema build, so adding a resource that brings one (`Questionnaire.item.item`) forces a choice.
- **Extensions** on resources only, with one nested level and one slot per open value type. All 50 value types
  would make one extension wider than a resource.
- **`modifierExtension`** in the nested Extension shape on a resource, and `url` only on a backbone element.
- **`contained`** left out. FHIR forbids nesting it, so it is a width question, not a loop.

The depth setting is removed. Which paths compile is decided by the shape a view returns (`DATA-MODEL-SUBSET`).

The schema still changes when a resource type is added, an extension value type is added, extensions are needed
on elements (a new rule, not a deeper cap), or the FHIR version changes. A measure reading a deeper path no
longer does.
