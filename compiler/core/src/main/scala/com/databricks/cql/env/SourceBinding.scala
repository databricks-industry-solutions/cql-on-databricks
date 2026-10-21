package com.databricks.cql.env

/** BIND-MANIFEST: one entry of the caller-supplied retrieve binding table — the SQL UDTF a FHIR resource
  * type binds to (`BIND-RETRIEVE-UDF`). The binding is DATA passed over the compile API (a per-type map
  * `fhirType` → `SourceBinding`); the compiler reads no catalog to discover a function or its signature
  * (`ARCH-PURITY`) — the name (and optional catalog) come from the payload. A type ABSENT from the map
  * resolves to the convention `retrieve_<Type>` in the default schema (`SqlEvalEnv.retrieveSource`).
  *
  * Extensible by design: `catalog` is optional, and future fields (`promotedColumns` for physical-column
  * filtering, coverage metadata) land here without a wire break — a lenient parse defaults optionals and
  * ignores unknown keys, so an old compiler tolerates a richer table and a new one a sparse table. */
final case class SourceBinding(function: String, catalog: Option[String] = None)
