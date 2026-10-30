# Data binding

How a compiled retrieve reaches real data. The binding is SQL the data owner writes. It needs no JVM code
and no change to the compiler.

## Retrieve functions — `BIND-RETRIEVE-UDF`, `BIND-EXPLICIT-PARAMS`

Every retrieve lowers to a call to a SQL table function:

```
[catalog.]retrieve_<Type>(value_set, code_property, codes, date_property, date_low, date_high, parameters)
```

| Parameter | Type | Meaning |
|---|---|---|
| `value_set` | `STRING` | The value-set id of a value-set-filtered retrieve |
| `code_property` | `STRING` | The FHIR element the code filter tests, resolved to its physical slot |
| `codes` | `ARRAY<STRUCT<system STRING, code STRING>>` | The declared codes of a code-filtered retrieve |
| `date_property` | `STRING` | The FHIR element the date filter applies to |
| `date_low`, `date_high` | `STRING` | The inclusive ISO bounds of a closed literal date window |
| `parameters` | `MAP<STRING, VARIANT>` | Each parameter the define's closure uses whose default is a compile-time literal, keyed by its CQL name in the define's own library. Empty for the context base |

Every argument is a literal, so Catalyst folds a guard on it at analysis time (`BIND-PUSHDOWN`). An absent
value is NULL or an empty array or map. The arguments are advisory (`BIND-CAPABILITY`): the compiled plan
re-applies every filter above the call, so a function that ignores them still returns a correct
superset. The signature is documented in `SqlQueryLowering.scala` and `SqlEvalEnv.retrieveParams`.

No argument names a library (`EMIT-ROOT-INDEPENDENT`). A function's rows must depend only on its arguments and
its data source's data, never on session state or the caller. Then a define's SQL, and its stored result, is
the same whichever library includes it (`DEC-RESULT-IDENTITY`).

A SQL table function inlines in Catalyst like a view, so column pruning and predicate pushdown reach the
base tables. Python and Scala table functions are opaque and block pushdown.

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
a named relation, so the same text is a session mount's function body, a published function's body, and the
loader's query.

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

CREATE OR REPLACE FUNCTION acme.cql_fhir.retrieve_Observation(<the seven retrieve parameters>)
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
- **Pushdown.** The functions ignore the pushdown arguments. The whole table is a correct superset
  (`BIND-CAPABILITY`).
- **Change logs** are optional in the layout, and publish writes none.

## The binding table — `BIND-MANIFEST`

The caller passes the binding as data in the compile request, under `sources`:

```json
{
  "sources": {
    "Encounter": { "function": "retrieve_Encounter", "catalog": "my_catalog.fhir" },
    "Condition": { "function": "retrieve_Condition", "catalog": "my_catalog.fhir" },
    "valuesets": { "table": "my_catalog.terminology.valueset" },
    "udfs": { "schema": "my_catalog.runtime" }
  }
}
```

Each type entry names a function and an optional catalog. A type with no entry uses `retrieve_<Type>`,
unqualified (`SqlEvalEnv.retrieveSource`). `valuesets.table` names the value-set expansion table
(`BIND-TERMINOLOGY-TABLE`). `udfs.schema` names the schema of the runtime functions
(`EMIT-UDF-QUALIFY`). The Python client builds this object (`CompileRequest._sources_json`).

## Value sets — `BIND-TERMINOLOGY-TABLE`

Value-set expansions are one table of `(valueset_id, system, code)` rows. A membership test selects its
set with `WHERE valueset_id = '<id>'` and lowers to a semi-join against it
(`SqlQueryLowering.membershipSemiJoin`). A compile host built without terminology rejects any membership
test rather than emit a wrong plan (`CORE-NO-SILENT-MISLOWER`). The local compile host is built that way.

## Selecting a measure's data

A data source is one population. To compute a measure over a different set of patients, bind a different
data source. The example patients the deploy loads are one data source, `synthetic-population-small`
(`DATA-SYNTH-SMALL`), that every example measure runs over. The data source's address
is part of every result's fingerprint (`RUN-CLIENT-RESULT-IDENTITY`), so two populations never share a result.

The same compiled plan runs against any data source with this contract: the example patients or your own
tables. Only the bound functions differ.

## Finding data sources

On Databricks, data sources are Unity Catalog schemas tagged `cql_databricks_data_source`, holding
`retrieve_<Type>` functions (`BIND-DATA-SOURCE-LAYOUT`). The client finds them by tag. See the
[client data sources doc](../client/docs/data-sources.md) and the
[Data Bindings README](../client/src/cql_databricks/data_bindings/README.md).
