---
name: import-quality-measures
description: "Import clinical quality measures (FHIR Measure and Library resources with their CQL, and the codes of the value sets they use) into Unity Catalog so CQL on Databricks can run them. Use when someone wants to load, add, upload or bring in a quality measure, measure package, CQL library or value-set codes, or asks where they can put measures they have."
---

<!--
  CONTENT-IMPORT-SKILL — the workflow for importing quality measures with CONTENT-IMPORT-MEASURES.
  A runtime asset: the agent loads it verbatim through get_skill.
-->

# Import quality measures

You help a user load quality measures into Unity Catalog for CQL on Databricks. A measure is a FHIR `Measure`
resource, the `Library` resources that hold its CQL, and the codes of every value set that CQL names. One
function writes all of it: `cql_databricks.data_bindings.measure_import.import_measures`. Your job is everything
around that call: finding the content, getting the value-set codes, and choosing a target the user is allowed to
write.

Work in a notebook cell by cell. Show the user each cell that writes before you run it.

## 1. Understand what the user has

Ask what they want to import and where it is, unless they already said. Then look at it before you plan:

- Work out how to reach it from this workspace, given what they told you and what you can see. Read it with the
  tool that fits where it is. Don't guess a location. If you can't reach it, say what you tried and ask.
- Work out its form: one FHIR `Bundle`, a folder or archive of FHIR JSON resources, value-set resources, rows of
  codes, or only CQL text. Packages often ship each resource twice, as JSON and XML; read the JSON.
- Measure content and code systems carry their own terms. Before you import, tell the user that the content's
  owner sets the terms, and ask them to confirm they may use it here. Don't copy it anywhere beyond the
  import's target.

If they only have CQL, each library needs a `Library` resource: the CQL as a `text/cql` attachment (base64),
and a canonical `url`, `name` and `version`. Ask the user which canonical base to use. A library becomes a
measure in the catalog only when a `Measure` resource names it.

## 2. Install the import tools

The import function is in the CQL on Databricks client. The deployment keeps the client's wheel in its runtime
Volume. Find that Volume:

```sql
SELECT volume_catalog, volume_schema, volume_name
FROM system.information_schema.volumes
WHERE comment LIKE 'CQL on Databricks runtime artifacts%';
```

If there is one, install from it. If there are several, ask the user which deployment to use. If there are none,
the user can't read it: ask them, or their admin, for its path. Its schema is the deployment's runtime schema,
which you need in step 7.

```python
%pip install /Volumes/<volume_catalog>/<volume_schema>/<volume_name>/wheels/cql_databricks-*.whl
dbutils.library.restartPython()
```

## 3. Read the resources

Build one Python list of resource dicts. `import_measures` flattens Bundles itself and imports `Measure`,
`Library` and `ValueSet` resources; it counts anything else in `skipped` and ignores it.

```python
from cql_databricks.data_bindings.measure_import import declared_valuesets, flatten

resources = [...]   # the dicts you read
libraries = [r for r in flatten(resources) if r.get("resourceType") == "Library"]
needed = set().union(*(declared_valuesets(lib) for lib in libraries))
```

## 4. Get the value-set codes

Every value set the libraries declare (`needed`) must have codes, in this import or already in the target's
`valueset` table. A measure run without them under-counts silently, so the import refuses.

- Check which ones already have codes in the terminology schema you will use (step 5):
  `SELECT DISTINCT valueset_id FROM <catalog>.<schema>.valueset`.
- For the rest, find out what the user has. A `ValueSet` resource with an `expansion`, or with a `compose` that
  only lists concepts, can be imported as is. One defined by filters or by other value sets must be expanded
  first, by a terminology service that can expand it (FHIR `ValueSet/$expand`). Ask the user which service
  they use and how they sign in to it. Their own key belongs in a secret scope, never in the notebook.
- Codes the user already has as rows go in `codes=`: dicts with `valueset_id` (the canonical URL, no
  `|version`), `system` and `code`, and optionally `display` and `version`.
- Never write codes yourself or guess an expansion.

## 5. Choose where it goes

The user must be able to write the target, and CQL on Databricks must be able to find it. Look before you
propose:

```sql
-- Schemas that already hold measures or value sets for CQL on Databricks
SELECT catalog_name, schema_name, tag_name, tag_value
FROM system.information_schema.schema_tags
WHERE tag_name IN ('cql_databricks_knowledge', 'cql_databricks_terminology');

-- What you can do in each schema (your own grants and your groups')
SELECT catalog_name, schema_name, collect_set(privilege_type) AS privileges
FROM system.information_schema.schema_privileges
WHERE grantee = current_user() OR is_account_group_member(grantee)
GROUP BY ALL;

-- Schemas you own, and catalogs where you can create a schema
SELECT catalog_name, schema_name FROM system.information_schema.schemata
WHERE schema_owner = current_user() OR is_account_group_member(schema_owner);
SELECT catalog_name, collect_set(privilege_type) AS privileges
FROM system.information_schema.catalog_privileges
WHERE grantee = current_user() OR is_account_group_member(grantee)
GROUP BY ALL;
```

A target works when the user owns the schema, or has `USE CATALOG`, `USE SCHEMA`, `CREATE TABLE`, `MODIFY`,
`SELECT` and `APPLY TAG` on it (`ALL PRIVILEGES` covers them). Privileges granted on the catalog apply to its
schemas too. With `CREATE SCHEMA` on a catalog they can make a new schema, which they then own.

The deployment finds measures only in its catalog scopes, a list of catalogs its deployer set. A target outside
them loads, but the Apps won't see it. Catalogs that already hold tagged knowledge schemas (the first query) are
likely in scope. Ask the user which catalogs the deployment uses, and say if they aren't sure. Propose one or two targets, with the reason, and let the user choose. Value
sets may share a terminology schema apart from the measures (`terminology_schema=`), so several measure sets keep
one copy of each value set. Re-importing replaces a value set's codes whole, and merges measures and libraries
on their canonical URL and version.

## 6. Dry run, then import

```python
from cql_databricks.data_bindings.measure_import import import_measures

summary = import_measures(spark, resources, codes=codes, catalog="<catalog>", schema="<schema>",
                          terminology_schema=None, source="<a short name for where this came from>",
                          dry_run=True)
summary
```

The function checks everything before it writes, and an `ImportContentError` lists every problem at once: a
measure naming a library that isn't there, a value set without codes, a value set that still needs expanding.
Fix them with the user, then run again. When the dry run is clean, show the summary, confirm the target, and run
the same call without `dry_run`.

## 7. Check it

```python
from cql_databricks.compiler import connect

client = connect(spark, catalog_scopes=["<catalog>"], valueset_table="<catalog>.<terminology schema>.valueset")
query = client.cql(library="<the measure's library name>")
query.populations()   # compiles the library with its includes, and lists the measure's populations
```

If the user wants numbers, run it over a data source they choose:
`query.with_measurement_period("<start>", "<end>").exec()`.

Report what was imported: the measures, the libraries, how many value sets, and anything skipped. Then
explain how the rest of the deployment sees it:

- In notebooks, `cql(library=...)` finds the libraries straight away, within the catalog scopes.
- Runs that don't name `valueset_table` read value sets from the knowledge views in the runtime schema, the
  schema of the Volume you found in step 2. The views pick up a new terminology schema when their owner rebinds
  them: the Designer's Bind action, or the next deploy. Until then, pass `valueset_table` as above.
