# Data binding

How a compiled retrieve reaches real data. The binding is SQL the data owner writes. It needs no JVM code
and no change to the compiler.

## Relations — `BIND-RELATION`, `BIND-RETRIEVE-REQUEST`, `BIND-NAMED-ARGS`

Each retrieve in the compiled CQL is answered by a data provider (`CLIENT-DATA-PROVIDER`). The client asks it once
per distinct retrieve, with a `RetrieveRequest`:

| Field | Type | Meaning |
|---|---|---|
| `fhir_type` | `STRING` | The FHIR resource type retrieved |
| `value_set` | `STRING` | The value-set id of a value-set-filtered retrieve |
| `codes` | `ARRAY<STRUCT<system STRING, code STRING>>` | The declared codes of a code-filtered retrieve |
| `date_property` | `STRING` | The FHIR element the date filter applies to |
| `date_low`, `date_high` | `STRING` | The inclusive ISO bounds of a closed literal date window |
| `parameters` | `MAP<STRING, VARIANT>` | Each parameter the define's closure uses whose default is a compile-time literal, keyed by its CQL name in the define's own library. Empty for the context's own retrieve |

An absent value is NULL or an empty array or map. A new field gets a default and a retired one stays, so a
provider written today keeps working. The fields are advisory (`BIND-CAPABILITY`): the compiled plan
re-applies every filter above the relation, so a provider may ignore all but `fhir_type`. What it returns
must hold every resource of that type that matches the request. A relation missing one gives a wrong
result with no error.

No field names a library (`EMIT-ROOT-INDEPENDENT`). A relation's rows depend only on the request and the
provider's data, never on session state or the caller, so a define's SQL, and its stored result, is the
same whichever library includes it (`DEC-RESULT-IDENTITY`).

The provider returns a relation. It names where the rows are and never carries a field mapping:

| Kind | Names | Runs on |
|---|---|---|
| `TableRelation` | A Delta table or UC view on the contract | Warehouse and Spark |
| `FunctionRelation` | A UC SQL table function on the contract | Warehouse and Spark |
| `VolumeRelation` | A volume folder of `<Type>.ndjson` files or FHIR Bundles, read with `read_files` | Warehouse and Spark |
| `DataFrameRelation` | A DataFrame | Spark only |

A volume or DataFrame relation is checked and reshaped (below), then registered as a temporary view
under a name the client owns, on the session that runs the SQL. So the compiler only ever sees table and
function names. A DataFrame relation on a SQL warehouse run fails before the run, naming the provider.
Nothing is materialized.

A function is called with only the parameters it declares, by name, each taking the request field of the
same name:

```sql
acme.cql_fhir.retrieve_Observation(value_set => 'http://…/ValueSet/a1c', date_low => '2026-01-01')
```

The client reads a function's parameters from `information_schema.parameters`. A declared parameter that
names no request field must have a default, and is left to it. One without a default fails before the run.
Every argument is a literal, so Catalyst folds a guard on it at analysis time (`BIND-PUSHDOWN`). A SQL
table function inlines in Catalyst like a view, so column pruning and predicate pushdown reach the base
tables. Python and Scala table functions are opaque and block pushdown.

The compiler never reads the catalog, builds a scan or holds a `SparkSession` (`ARCH-PURITY`).

## The contract — `BIND-CONTRACT`

Every retrieve function returns two columns:

- `__subject_id STRING`: the context key (the Patient id, or the resolved subject reference).
- `__resource STRUCT`: the whole resource, with the schema the compiler derives from the FHIR model
  ([data-model.md](data-model.md)), plus `__resource_type` and `__resource_id`.

The compile service App serves that schema per type from `/resource-schema` (`SVC-RESOURCE-SCHEMA`), so a binding
can declare `RETURNS TABLE` to match. The Python client exposes it as `client.resource_schema(type)`
(`CLIENT-RESOURCE-SCHEMA`).

## Reshaping data to the contract — `BIND-RESHAPE`, `BIND-SCHEMA-CHECK`

One implementation turns FHIR data into the contract: `cql_databricks.compiler.reshape`. It generates SQL over
a named relation, so the same text is the view behind a volume or DataFrame relation, a published function's
body, and the loader's query.

| Source | How it is read |
|---|---|
| JSON text, one resource per row | `from_json` against the model's DDL. Missing fields read as null; extra fields are dropped. Parsed on every read |
| A structured relation, one type | The model's struct rebuilt field by field: a missing field is a typed null, an extra one dropped, a safe widening (`int` to `long`, a date to its ISO string) cast |
| A relation already on the contract | Its `__resource` rebuilt the same way; its own `__subject_id` kept |

`__resource_type` is the type and `__resource_id` is `id`. `__subject_id` is `id` for Patient, the trailing
id of `subject`, `patient` or `beneficiary` `.reference` for a Patient-compartment type (`Patient/<id>`,
`urn:uuid:<id>`, full URLs), and null otherwise.

The same walk is the check. It names every problem at once:

| Finding | Severity |
|---|---|
| A modeled field is missing | WARN, reads as null |
| A safe widening; a field not in the model | INFO |
| A type no cast fixes safely (a timestamp for a FHIR `dateTime` string) | ERROR |
| A merged choice field, `value` for `valueQuantity`, `valueString`, … | ERROR |
| A reference or other struct stored as a JSON string (as dbignite writes them) | ERROR |
| No `id`, or no reference to derive `__subject_id` from | ERROR |

A source with an ERROR is refused. Checking the fields one query demands (`BIND-CAPABILITY-OVERLAP`) is not
built.

## Publishing a data source — `BIND-PUBLISH`

`cql_databricks.compiler.publish_data_source` writes a data source's declaration over tables, never data. It
returns a plan: the SQL, and every finding of the check above. `run()` refuses on any ERROR, then runs, as the
caller:

```sql
CREATE SCHEMA IF NOT EXISTS acme.cql_fhir;

CREATE OR REPLACE FUNCTION acme.cql_fhir.retrieve_Observation()
RETURNS TABLE (__subject_id STRING, __resource <the model's struct>)
LANGUAGE SQL
RETURN <the reshape of acme.silver.observation>;

ALTER SCHEMA acme.cql_fhir SET TAGS ('cql_databricks_data_source' = 'acme_fhir');
COMMENT ON SCHEMA acme.cql_fhir IS 'Acme FHIR R4, nightly';   -- with description=
```

Then it re-reads the schema and checks it against the layout (`BIND-DATA-SOURCE-LAYOUT`).

- **Tables only.** A function can't name a session DataFrame or files read on the driver. Raw JSON is
  refused too: a function over it would parse JSON on every query. Write `FhirJson(...).to_df(spark, type)`
  to a table and publish that.
- **Pushdown.** The functions declare no parameters (`BIND-NAMED-ARGS`). The whole table is a correct
  superset (`BIND-CAPABILITY`).
- **Change logs** are optional in the layout, and publish writes none.

## The compile request — `BIND-PROVIDER-REQUEST`

`describe` returns each retrieve with an id and its request. The id is the FHIR type and 12 hex digits of a
hash of the request, so two retrieves with the same request share one. The client asks the provider for each
and sends the relations, keyed by retrieve id, one field per role:

```json
{
  "data": {
    "Observation_3f2a9c01b7de": {"function": "acme.cql_fhir.retrieve_Observation",
                                 "parameters": ["value_set", "date_low", "date_high"],
                                 "identity": "acme.cql_fhir"},
    "Patient_0c41d2e6a95f":     {"table": "acme.cql_fhir.retrieve_Patient", "identity": "acme.cql_fhir"}
  },
  "terminology": [{"table": "acme.terminology.valueset", "identity": "acme.terminology"}],
  "knowledge": ["<dependency ELM>", {"cql": "…", "name": "Helpers", "version": "1.0.0"}],
  "runtime": {"schema": "acme.runtime"}
}
```

The compiler embeds each relation in the SQL it emits, so which relation each retrieve used is in the SQL.
A retrieve with no relation fails the compile, naming it. `identity` is the identity of the provider that
answered, which a kept result's fingerprint covers (`CLIENT-PROVIDER-IDENTITY`). `runtime.schema` qualifies
the `cql_*` calls (`EMIT-UDF-QUALIFY`). The client never patches the SQL the compiler returns.

## Value sets — `BIND-TERMINOLOGY-TABLE`

`terminology` is a list of relations of `(valueset_id, version, system, code)` rows, in precedence order. A
membership test reads its set from the first relation that holds it, `WHERE valueset_id = '<id>'`, and
lowers to a semi-join against it (`SqlQueryLowering.membershipSemiJoin`). So an `Overlay` of a development
terminology over a published one replaces a value set rather than merging it (`CLIENT-PROVIDER-OVERLAY`).
A membership test with no terminology fails rather than emit a wrong plan (`CORE-NO-SILENT-MISLOWER`).

## Selecting a measure's data

A data source is one population. To compute a measure over a different set of patients, bind a different
data source. The example patients the deploy loads are one data source, `synthetic-population-small`
(`DATA-SYNTH-SMALL`), that every example measure runs over. The data provider's identity
is part of every result's fingerprint (`CLIENT-PROVIDER-IDENTITY`), so two populations never share a result.

The same CQL runs against any data with this contract: the example patients or your own tables. Only the
relations differ.

## Finding data sources

On Databricks, data sources are Unity Catalog schemas tagged `cql_databricks_data_source`, holding
`retrieve_<Type>` tables, views or SQL functions (`BIND-DATA-SOURCE-LAYOUT`). `TaggedResolver` finds them by
tag and returns one by name (`CLIENT-DEFAULT-RESOLVER`). See the
[client data sources doc](../client/docs/data-sources.md) and the
[Data Bindings README](../client/src/cql_databricks/data_bindings/README.md).
