# Data binding

How a compiled retrieve reaches real data. The binding is SQL the data owner writes. It needs no JVM code
and no change to the compiler.

## Relations — `BIND-RELATION`, `BIND-RETRIEVE-REQUEST`

Each retrieve in the compiled CQL is a call site, answered by a data resolver (`CLIENT-DATA-RESOLVER`). A resolver
binds by rule, one schema of views for every type, or per site. A resolver that binds per site is asked once per
retrieve site, with a `RetrieveRequest` and the site's context (`BIND-CALL-SITE`):

| Field | Type | Meaning |
|---|---|---|
| `fhir_type` | `STRING` | The FHIR resource type retrieved |
| `value_set` | `STRING` | The value-set id of a value-set-filtered retrieve |
| `codes` | `ARRAY<STRUCT<system STRING, code STRING>>` | The declared codes of a code-filtered retrieve |
| `date_property` | `STRING` | The FHIR element the date filter applies to |
| `date_low`, `date_high` | `STRING` | The inclusive ISO bounds of a closed literal date window |
| `parameters` | `MAP<STRING, VARIANT>` | Each parameter the define's closure uses whose default is a compile-time literal, keyed by its CQL name in the define's own library. Empty for the context's own retrieve |

An absent value is NULL or an empty array or map. A new field gets a default and a retired one stays, so a
resolver written today keeps working. The fields are advisory (`BIND-CAPABILITY`): the compiled plan
re-applies every filter above the relation, so a resolver may ignore all but `fhir_type`. What it returns
must hold every resource of that type that matches the request. A relation missing one gives a wrong
result with no error.

A site's context names the library the retrieve is written in and its define, never the root being compiled
(`EMIT-ROOT-INDEPENDENT`). So a define's SQL, and its stored result, is the same whichever library includes it
(`DEC-RESULT-IDENTITY`).

The resolver returns a relation. It names where the rows are and never carries a field mapping:

| Kind | Names | Runs on |
|---|---|---|
| `TableRelation` | A Delta table or UC view on the contract | Warehouse and Spark |
| `VolumeRelation` | A volume folder of `<Type>.ndjson` files or FHIR Bundles, read with `read_files` | Warehouse and Spark |
| `DataFrameRelation` | A DataFrame | Spark only |

A volume or DataFrame relation is checked and reshaped (below), then registered as a temporary view
under a name the client owns, on the session that runs the SQL. So the compiler only ever sees table names. A
DataFrame relation on a SQL warehouse run fails before the run, naming the resolver. Nothing is materialized.

No table function is bound. A relation takes no arguments: the compiled plan applies the request's filters
above it, and Catalyst pushes them through a view into the scans under it (`BIND-PUSHDOWN`).

The compiler never reads the catalog, builds a scan or holds a `SparkSession` (`ARCH-PURITY`).

## The contract — `BIND-CONTRACT`

A data relation, a view or a table, returns one row per resource, with the resource as a struct column, `resource`:
any subset of FHIR R4 for its type (`DATA-MODEL-SUBSET`, [data-model.md](data-model.md)), named and typed as FHIR
JSON, holding FHIR's fields only. It holds the fields its data fills; CQL that reads a field it leaves out fails
the compile, naming the path. Beside it the relation may return three root columns, each optional, so the engine
reaches rows faster:

| Column | Type | Requirement |
|---|---|---|
| `resource` | struct | the resource, a faithful subset of FHIR R4. Required |
| `patient_id` | string or integer | the subject key (`DATA-SUBJECT-KEY`) |
| `min_date`, `max_date` | date | the coarse date filter ([filter and refine](#filter-and-refine--bind-prefilter), `BIND-PREFILTER`) |

Any other root column is refused, naming it, and so is any of the engine's own `__*` columns, at the root or at
the top of `resource` (`DATA-SYSTEM-COLUMNS`). How the view builds `resource` is the implementer's:

```sql
CREATE VIEW acme.data.condition AS
SELECT person_id AS patient_id,
       named_struct(
         'id', CAST(condition_id AS STRING),
         'code', named_struct('coding', array(named_struct('system', code_system, 'code', code))),
         'subject', named_struct('reference', concat('Patient/', person_id)),
         'onsetDateTime', CAST(onset_date AS STRING)) AS resource,
       least(onset_date, end_date) AS min_date,
       greatest(onset_date, end_date) AS max_date
FROM acme.ehr.conditions
```

The engine reads it as its own `(__subject_id, __resource)`: `__resource` is `resource`, passed through and never
rebuilt, and `__subject_id` is the row's patient_id (`DATA-SUBJECT-KEY`):

- on a Patient or a Patient-compartment type, the root `patient_id` when the view returns it;
- else FHIR's own field in `resource`: `id` on a Patient, or the id at the end of `subject.reference` (or
  `patient`, `beneficiary`) on a Patient-compartment type;
- null on a type with no patient context, such as Medication, whatever `patient_id` its view returns. One
  Medication can be named by many patients' MedicationRequests, so it belongs to no one patient.

A Patient-compartment view with neither `patient_id` nor a subject reference fails the compile, naming the fix.

`patient_id` works like a fully qualified key in an entity-relationship model: an optimization, used mostly for
joins and also for the subject filter. It is a promise: a view's `patient_id` is the id the other views'
references name, so `acme.data.patient` returns `person_id AS patient_id` too, or no `patient_id` and an `id` of
the same value. A `patient_id` that breaks the promise breaks joins with no error, as dates that break theirs drop
rows.

Where `resource` is built decides what a query pays for it. A table that stores the struct passes it through as
it is. A view that builds it from columns, like the one above, is inlined into every query that reads it, so the
warehouse plans the `named_struct` in each one. Measured on the default environment's warehouse (2026-11-03,
HypertensionBPControl, 4,000 patients): building the struct at the read cost 18–23% more end to end, all of it
Spark planning; execution was the same. For speed, write the view's rows to a table and publish over that.

The compile service App serves the stock shape per type from `/resource-schema` (`SVC-RESOURCE-SCHEMA`), the
`resource` struct the reshape and the store write. The Python client exposes it as `client.resource_schema(type)`
(`CLIENT-RESOURCE-SCHEMA`).

## Filter and refine — `BIND-PREFILTER`

A relation may return `min_date` and `max_date`, root date columns, so the engine can prune rows on columns the
warehouse filters and clusters on before the exact predicates run:

| Columns | Type | The promise | What the engine does |
|---|---|---|---|
| `min_date`, `max_date` | date | together they cover every date in the record | beside a retrieve's literal date range, keeps rows with `max_date >= low` and `min_date <= high`, each bound widened by a day for time zones |

The date test is the coarse filter. The retrieve's own date test, run after it, is the refine step and decides, so
a coarse filter that admits extra rows changes no answer. Dates that break their promise drop rows the CQL should
see. Measured on OSS Spark 4.0.1 (2026-11-03): a subject derived from a reference built with `concat` reached the
source table as no pushed filter, and the same filter on a stored key column reached the source column.

Delta collects file statistics for a table's first 32 columns by default, counting each struct field, and
`resource` alone has more. A stored table puts `patient_id`, `min_date` and `max_date` before `resource`, as the
reshape does, or lists them in `delta.dataSkippingStatsColumns`, and clusters on the key: `CLUSTER BY
(patient_id)`. Partitioning needs a top-level column, which these are.

## Reshaping data to the contract — `BIND-RESHAPE`, `BIND-SCHEMA-CHECK`

One implementation turns FHIR data into the contract, `(patient_id, resource)`: `cql_databricks.compiler.reshape`.
It generates SQL over a named relation, so the same text is the view behind a volume or DataFrame relation, a
published view's body, and the query the loader and the deploy write the store with. The store holds the contract
itself, so a published view over it selects its two columns and rebuilds nothing.

| Source | How it is read |
|---|---|
| JSON text, one resource per row | `from_json` against the model's DDL. Missing fields read as null; extra fields are dropped. Parsed on every read |
| A structured relation, one type | The model's struct rebuilt field by field: a missing field is a typed null, an extra one dropped, a safe widening (`int` to `long`, a date to its ISO string) cast |
| A relation on the contract | Its `resource` rebuilt the same way; its own `patient_id` kept |

`resource` holds FHIR's fields only. `patient_id` is the source's own `patient_id` when it has one, else `id` for
Patient, the trailing id of `subject`, `patient` or `beneficiary` `.reference` for a Patient-compartment type
(`Patient/<id>`, `urn:uuid:<id>`, full URLs), and null otherwise.

The same walk is the check. It names every problem at once:

| Finding | Severity |
|---|---|
| A modeled field is missing | WARN, reads as null |
| A safe widening; a field not in the model | INFO |
| A type no cast fixes safely (a timestamp for a FHIR `dateTime` string) | ERROR |
| A merged choice field, `value` for `valueQuantity`, `valueString`, … | ERROR |
| A reference or other struct stored as a JSON string (as dbignite writes them) | ERROR |
| No `id`, or no reference to derive `patient_id` from | ERROR |

A source with an ERROR is refused. Whatever the relation, the compile then checks every path a query reads
against the shape the relation returns (`DATA-MODEL-SUBSET`, `DATA-PATH-IN-SCHEMA`).

## Publishing data over tables — `BIND-PUBLISH`

`cql_databricks.compiler.publish_data_source` writes an environment's published views over the customer's tables,
never data. It returns a plan: the SQL, and every finding of the check above. `run()` refuses on any ERROR, then
runs, as the caller:

```sql
CREATE OR REPLACE VIEW acme.data.observation AS
<the reshape of acme.silver.observation: patient_id and resource>;
```

Each view replaces the one that selected the environment's store (`DATA-ENVIRONMENT-LAYOUT`).

- **Tables only.** A view can't name a session DataFrame or files read on the driver. Raw JSON is refused too: a
  view over it would parse JSON on every query. Write `FhirJson(...).to_df(spark, type)` to a table and publish
  that.
- **Pushdown.** A view takes no arguments; it returns its whole table, a correct superset (`BIND-CAPABILITY`), and
  Catalyst pushes the compiled plan's filters into it.
- **Change logs** are optional in the layout, and publish writes none.

## The compile request — `BIND-CALL-SITE`

The compiler knows nothing of environments, users or results. It reports call sites and binds them from the
request. `describe` returns every call site: each retrieve with its request, each value-set reference, and each
library include, each with the library it is written in and its define. A site's id is its kind and 12 hex digits
of a hash of that context, its position in the define and its request.

Each role carries a `rule`, a `sites` map, or both. For each site the compiler uses the map's entry if there is one,
else the rule. A run in an environment sends only rules, and makes one compiler call:

```json
{
  "data": {"rule": {"schema": "acme.data", "identity": "acme.data"},
           "shapes": {"observation": "struct<patient_id:string,resource:struct<id:string,status:string,code:struct<…>,…>>"}},
  "terminology": {"rule": [{"table": "acme.knowledge.valueset", "identity": "acme.knowledge"}]},
  "knowledge": ["<dependency ELM>", {"cql": "…", "name": "Helpers", "version": "1.0.0"}],
  "runtime": {"schema": "acme.runtime"}
}
```

The data rule binds a retrieve of `Observation` to `acme.data.observation`. A resolver that binds per site calls
`describe` first, then sends its answers by site id:

```json
{
  "data": {
    "rule": {"schema": "acme.data", "identity": "acme.data"},
    "sites": {"retrieve_3f2a9c01b7de": {"table": "hist.data.condition_archive", "identity": "hist.data"}}
  }
}
```

`shapes` holds the type of each bound type's relations' columns (`DATA-MODEL-SUBSET`). The compiler reads no
catalog, so the client reads them: every column of an environment's views in one `information_schema` query, once
per client session
(about 1.5 s warm on a SQL warehouse, measured 2026-11-03), a table bound per site from its catalog's
`information_schema` at each bind, and a volume or DataFrame from the stock shape it reshapes it to. `DESCRIBE TABLE`
is not read: it cuts a struct of many fields short. Two relations of one type with different shapes fail before the
compile.

The compiler embeds each relation in the SQL it emits, so which relation each site used is in the SQL. A site with
no relation fails the compile, naming it. `identity` is the identity of the resolver that answered, which a kept
result's fingerprint covers (`CLIENT-RESOLVER-IDENTITY`). `runtime.schema` qualifies the `cql_*` calls
(`EMIT-UDF-QUALIFY`). The client never patches the SQL the compiler returns.

## Value sets — `BIND-CALL-SITE`

A terminology rule or site entry is a list of relations of `(valueset_id, version, system, code)` rows, in
precedence order. A membership test reads its set from the first relation that holds it,
`WHERE valueset_id = '<id>'`, and lowers to a semi-join against it (`SqlQueryLowering.membershipSemiJoin`). So an
`Overlay` of a development terminology over a published one replaces a value set rather than merging it
(`CLIENT-RESOLVER-OVERLAY`). A membership test with no terminology fails rather than emit a wrong plan
(`CORE-NO-SILENT-MISLOWER`).

## Selecting a measure's data

An environment's data is one population. To compute a measure over a different set of patients, run in a different
environment, or pass a different data resolver. The default environment holds the example patients,
`DATA-SYNTH-SMALL`, that every example measure runs over. The data resolver's identity is part of every result's
fingerprint (`CLIENT-RESOLVER-IDENTITY`), so two populations never share a result.

The same CQL runs against any data with this contract: the example patients or your own tables. Only the
relations differ.

## Finding data — `CLIENT-ENVIRONMENT`

On Databricks, data is an environment's `data` schema: a catalog tagged `cql_databricks_environment`, with one
published view per FHIR type (`DATA-ENVIRONMENT-LAYOUT`). `EnvironmentResolver` finds environments by tag and
returns the default or one by name (`CLIENT-ENVIRONMENT-RESOLVER`). See the
[client data sources doc](../client/docs/data-sources.md).
