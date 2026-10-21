package com.databricks.cql.runtime.udf

/** EXEC-UDF-REGISTRY-CACHE (compiler/docs/execution-model.md) — expensive per-JVM registries
  * (UCUM essence, timezone data, and, if ever bridged, a HAPI `FhirContext`) built ONCE per executor
  * JVM as lazy singletons, never per partition or per row. A UDF that rebuilt UCUM per call would
  * reproduce the "2 minutes for a handful of patients" trap CQF avoids by parsing once into a heap
  * object; because this object is a Scala `object`, each `lazy val` initializes at most once per
  * classloader (== once per executor JVM after the catalog JAR is shipped there).
  *
  * SCAFFOLD (Phase 1): the timezone cache is real (pure JDK, no dependency); the UCUM essence is a
  * placeholder the Phase-4 normalized-type work fills once a UCUM implementation is added to this
  * module's (deliberately minimal) classpath and shipped alongside the catalog JAR. The lazy-singleton
  * SHAPE is fixed now so filling it in later is a one-line change with no call-site churn. */
object RegistryCaches {

  /** Timezone-offset lookups, memoized per JVM. `ZoneId.of` parses/validates on every call; the
    * temporal UDFs (Phase 4) resolve a bounded set of zone ids repeatedly, so cache the parse. */
  private val zoneCache = new java.util.concurrent.ConcurrentHashMap[String, java.time.ZoneId]()

  def zone(id: String): java.time.ZoneId =
    zoneCache.computeIfAbsent(id, k => java.time.ZoneId.of(k))

  /** UCUM canonicalization registry — Phase 4 placeholder (EXEC-CANON-TYPE quantity normalization).
    * Kept as a lazy singleton so the eventual heavy essence load happens once per executor JVM. */
  lazy val ucum: UcumRegistry = new UcumRegistry
}

/** Placeholder for the UCUM essence-backed unit canonicalizer (Phase 4). Intentionally inert now — it
  * exists so [[RegistryCaches.ucum]] has a stable type and the once-per-JVM load site is already in
  * place. */
final class UcumRegistry {
  def canonicalize(value: java.math.BigDecimal, unit: String): (java.math.BigDecimal, String) =
    (value, unit) // identity until Phase 4 wires the UCUM service
}
