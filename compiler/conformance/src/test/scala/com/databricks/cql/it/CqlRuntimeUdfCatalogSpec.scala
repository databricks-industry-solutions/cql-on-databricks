package com.databricks.cql.it

import com.databricks.cql.runtime.udf.CqlRuntimeUdfs
import com.databricks.cql.runtime.eval.CqlOps
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** EXEC-UDF-LIBRARY / EXEC-UDF-CATALOG (compiler/docs/execution-model.md) — the opaque-UDF catalog gate. Confirms the
  * catalog's shape (version pin, the emit↔runtime name agreement) and that the ported struct-param op
  * `cql_convert_quantity` — shipped to the Connect server and registered by name through the session seam
  * ([[SparkConnect.installUdfCatalog]]) — executes with its `CqlQuantity` case-class param (`EXEC-UC-STRUCT-ARG`,
  * the same handler the UC backend uses). Heterogeneous-Choice navigation is NOT a UDF anymore (it lowers
  * natively, `EMIT-SQL-FLAT-CHOICE`); its semantics are gated by the corpus SQL differential on the choice
  * fixtures, not here. Server-executing tests SKIP cleanly when no Connect server is reachable. */
@RunWith(classOf[JUnitRunner])
final class CqlRuntimeUdfCatalogSpec extends AnyFunSuite {

  test("the catalog is version-pinned (EXEC-UDF-LIBRARY)") {
    assert(CqlRuntimeUdfs.Version.nonEmpty)
    assert(CqlRuntimeUdfs.catalog.contains("cql_temporal_cmp"))
  }

  test("the catalog holds exactly the emitted UDF names (emit↔runtime agreement)") {
    // Every name the compiler emits (SqlNames) must resolve in the runtime catalog, and nothing else.
    val expected = Set("cql_temporal_cmp", "cql_temporal_same_prec", "cql_temporal_step", "cql_date_add",
      "cql_age_at", "cql_temporal_boundary", "cql_to_interval", "cql_convert_quantity", "cql_variant_valid")
    assert(CqlRuntimeUdfs.catalog.keySet == expected,
      s"catalog=${CqlRuntimeUdfs.catalog.keySet}")
  }

  // ---- cql_variant_valid schema-conformance semantics (EMIT-SQL-VARIANT-CHOICE, pure — no Spark session) ----

  test("cql_variant_valid: a sparse variant payload conforms to the FHIR-model schema (EMIT-SQL-VARIANT-CHOICE)") {
    // A het-Choice payload: ServiceRequest/MedicationRequest carry `authoredOn` (a dateTime → STRING);
    // the Procedure arm carries `performedPeriod` (a Period struct). schema_of_variant infers OBJECT<…> with
    // fields SORTED and `end` (a SQL keyword) back-tick-quoted; VOID appears for a null field.
    val expected = "STRUCT<authoredOn: STRING, performedPeriod: STRUCT<start: STRING, end: STRING>, " +
      "performedString: STRING, category: ARRAY<STRUCT<text: STRING>>>"
    // A ServiceRequest arm: only authoredOn present.
    assert(CqlOps.variantValid("OBJECT<authoredOn: STRING>", expected))
    // A Procedure arm: only the Period present, keyword field quoted, fields in variant's sorted order.
    assert(CqlOps.variantValid("OBJECT<performedPeriod: OBJECT<`end`: STRING, start: STRING>>", expected))
    // A null field infers VOID — still conforms (the slot is declared).
    assert(CqlOps.variantValid("OBJECT<authoredOn: VOID>", expected))
    // A repeating (array) slot kept in the payload conforms element-wise.
    assert(CqlOps.variantValid("OBJECT<category: ARRAY<OBJECT<text: STRING>>>", expected))
    // Empty payload (a null variant / no present slots) trivially conforms.
    assert(CqlOps.variantValid("OBJECT<>", expected))
  }

  test("cql_variant_valid: an UNDECLARED field or a type clash is a violation (fail-closed)") {
    val expected = "STRUCT<authoredOn: STRING, performedPeriod: STRUCT<start: STRING, end: STRING>>"
    // A field that is NOT in the model schema landed in the opaque payload → violation.
    assert(!CqlOps.variantValid("OBJECT<bogusField: STRING>", expected))
    // A declared field whose type clashes with the model (struct where a scalar is expected) → violation.
    assert(!CqlOps.variantValid("OBJECT<authoredOn: OBJECT<x: STRING>>", expected))
    // A nested-struct field that is undeclared → violation.
    assert(!CqlOps.variantValid("OBJECT<performedPeriod: OBJECT<middle: STRING>>", expected))
    // Malformed / null input fails closed.
    assert(!CqlOps.variantValid("not a ddl <<<", expected))
    assert(!CqlOps.variantValid(null, expected))
  }

  test("cql_variant_valid executes over Connect with schema_of_variant (EMIT-SQL-VARIANT-CHOICE)") {
    val spark = SparkConnect.session()
    val r = spark.sql(
      "SELECT cql_variant_valid(schema_of_variant(to_variant_object(named_struct('authoredOn', '2026-01-01'))), " +
      "'STRUCT<authoredOn: STRING, performedPeriod: STRUCT<start: STRING, end: STRING>>') AS ok, " +
      "cql_variant_valid(schema_of_variant(to_variant_object(named_struct('bogus', 'x'))), " +
      "'STRUCT<authoredOn: STRING>') AS bad").collect().head
    assert(r.getBoolean(0), "sparse authoredOn payload should conform to the model schema")
    assert(!r.getBoolean(1), "an undeclared field that landed should be flagged")
  }

  test("cql_choice_nav_* is retired — choice navigation is native (EMIT-SQL-FLAT-CHOICE)") {
    assert(!CqlRuntimeUdfs.catalog.keySet.exists(_.startsWith("cql_choice_nav")))
  }

  test("cql_convert_quantity executes over Connect with a CqlQuantity case-class param (EXEC-UC-STRUCT-ARG)") {
    val spark = SparkConnect.session()
    // 2 mg -> g with factor 0.001 => {0.002, 'g'}; an unmodeled unit yields NULL.
    val r = spark.sql(
      "SELECT cql_convert_quantity(named_struct('value', CAST(2 AS DECIMAL(38,18)), 'unit', 'mg'), " +
      "map('mg', CAST(0.001 AS DECIMAL(38,18))), 'g') AS ok, " +
      "cql_convert_quantity(named_struct('value', CAST(2 AS DECIMAL(38,18)), 'unit', 'xx'), " +
      "map('mg', CAST(0.001 AS DECIMAL(38,18))), 'g') AS miss").collect().head
    val ok = r.getStruct(0)
    assert(ok != null && ok.getString(1) == "g", s"unit=${Option(ok).map(_.getString(1))}")
    assert(ok.getDecimal(0).compareTo(new java.math.BigDecimal("0.002")) == 0, s"value=${ok.getDecimal(0)}")
    assert(r.get(1) == null, s"unmodeled unit should be null, got ${r.get(1)}")
  }
}
