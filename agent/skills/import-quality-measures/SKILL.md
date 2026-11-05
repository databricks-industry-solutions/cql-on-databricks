---
name: import-quality-measures
description: "Import clinical quality measures (FHIR Measure and Library resources with their CQL, and the codes of the value sets they use) into a folder of the user's own, so CQL on Databricks can run them from there. Use when someone wants to load, add, upload or bring in a quality measure, measure package, CQL library or value-set codes, or asks where they can put measures they have."
---

<!--
  CONTENT-IMPORT-SKILL — the workflow for importing quality measures with CONTENT-IMPORT-MEASURES.
  A runtime asset: the agent loads it verbatim through get_skill.
-->

# Import quality measures

You help a user load quality measures into a folder of their own, so CQL on Databricks can run them. A measure is a
FHIR `Measure` resource, the `Library` resources that hold its CQL, and the codes of every value set that CQL names.
One function writes all of it, as files: `cql_databricks.data_bindings.measure_import.import_measures`. Your job is
everything around that call: finding the content, getting the value-set codes, and choosing a folder the user may
write.

The import never writes an environment's `knowledge` tables. What is there is the deployment's and the
implementer's; making a measure official is their process, not this one. If the user asks to publish into an
environment, say so, and import into their folder.

Work in a notebook cell by cell. Show the user each cell that writes before you run it.

## 1. Understand what the user has

Ask what they want to import and where it is, unless they already said. Then look at it before you plan:

- Work out how to reach it from this workspace, given what they told you and what you can see. Read it with the
  tool that fits where it is. Don't guess a location. If you can't reach it, say what you tried and ask.
- Work out its form: one FHIR `Bundle`, a folder or archive of FHIR JSON resources, value-set resources, rows of
  codes, or only CQL text. Packages often ship each resource twice, as JSON and XML; read the JSON.
- Measure content and code systems carry their own terms. Before you import, tell the user that the content's
  owner sets the terms, and ask them to confirm they may use it here. Don't copy it anywhere beyond the
  import's folder.

If they only have CQL, each library needs a `Library` resource: the CQL as a `text/cql` attachment (base64),
and a canonical `url`, `name` and `version`. Ask the user which canonical base to use. A library is a measure
only when a `Measure` resource names it.

## 2. Install the import tools

The import function is in the CQL on Databricks client. The deployment keeps the client's wheel in its runtime
Volume. Find that Volume:

```sql
SELECT volume_catalog, volume_schema, volume_name
FROM system.information_schema.volumes
WHERE comment LIKE 'CQL on Databricks runtime artifacts%';
```

If there is one, install from it. If there are several, ask the user which deployment to use. If there are none,
the user can't read it: ask them, or their admin, for its path.

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

Every value set the libraries declare (`needed`) must have codes, in this import or already in the folder's
`valuesets/`. A measure run without them fails, so the import refuses.

- A folder imported into before already holds some: the `url` of each `valuesets/*.json` file.
- For the rest, find out what the user has. A `ValueSet` resource with an `expansion`, or with a `compose` that
  only lists concepts, can be imported as is. One defined by filters or by other value sets must be expanded
  first, by a terminology service that can expand it (FHIR `ValueSet/$expand`). Ask the user which service
  they use and how they sign in to it. Their own key belongs in a secret scope, never in the notebook.
- Codes the user already has as rows go in `codes=`: dicts with `valueset_id` (the canonical URL, no
  `|version`), `system` and `code`, and optionally `display` and `version`. `version` is the ValueSet's own
  version, the one a CQL declaration pins with `version '<v>'`; a value set with several versions needs each CQL
  declaration of it to pin one, or the run fails.
- Never write codes yourself or guess an expansion.

## 5. Choose the folder

The import writes files into a folder the user owns and can write: a folder in their workspace home
(`/Workspace/Users/<their user name>/...`) or a Unity Catalog volume they have `WRITE VOLUME` on
(`/Volumes/<catalog>/<schema>/<volume>/...`). Use a new folder per measure set, propose a name from what they are
importing, and let the user choose. It holds:

- `libraries/<Name>.cql`, each library's CQL;
- `resources/library-<Name>-<version>.json` and `resources/measure-<Name>-<version>.json`, the resources as given;
- `valuesets/<name>[-<version>].json`, one FHIR `ValueSet` per value set and version, its codes as its `expansion`.

Importing again replaces the files of the same library or value set and version. A library file holding another
version of the library is refused, never overwritten: import that version into another folder.

## 6. Dry run, then import

```python
from cql_databricks.data_bindings.measure_import import import_measures

FOLDER = "<the folder>"
summary = import_measures(resources, codes=codes, folder=FOLDER, dry_run=True)
summary
```

The function checks everything before it writes, and an `ImportContentError` lists every problem at once: a
measure naming a library that isn't there, a value set without codes, a value set that still needs expanding.
Fix them with the user, then run again. When the dry run is clean, show the summary, confirm the folder, and run
the same call without `dry_run`.

## 7. Check it

```python
from cql_databricks.compiler import cql, use_resolvers
from cql_databricks.resolvers import FolderKnowledgeResolver, FolderTerminologyResolver

use_resolvers(knowledge=FolderKnowledgeResolver(FOLDER), terminology=FolderTerminologyResolver(f"{FOLDER}/valuesets"))
query = cql(library="<the measure's library name>")
query.populations()   # compiles the library with its includes, and lists the measure's populations
```

Its includes must be in the folder too, `FHIRHelpers` among them: import them with the measure. If the user wants
numbers, run it over the environment's data: `query.with_measurement_period("<start>", "<end>").exec()`.
`use_resolvers()` goes back to the environment's knowledge.

Report what was imported: the measures, the libraries, how many value sets, the folder, and anything skipped. Then
explain how the rest of the deployment sees it:

- In notebooks, `use_resolvers` as above runs it from the folder; the folder's owner decides who else may read it.
- If it is a workspace folder, the Designer opens it there, as a library to edit.
- It is not in any environment's knowledge, so Genie and the agent's tools don't find it by name, and its results
  are never official. Putting it in an environment is the implementer's process.
