package com.databricks.cql.it

import com.databricks.cql.runtime.udf.{CqlRuntimeUdfs, UcFunctionManifest}
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** EXEC-UC-MANIFEST / EXEC-UC-STRUCT-ARG (compiler/docs/execution-model.md) — the Spark-free consistency gate on
  * the UC-deployment function manifest. It proves the manifest is internally coherent and matches the
  * catalog, so a downstream host can generate `CREATE FUNCTION … HANDLER …` DDL from it without guessing:
  *  - every `sqlName` is a real emitted `cql_*` name (present in [[CqlRuntimeUdfs.catalog]]);
  *  - every `handler` FQN resolves to a real public method on `…runtime.eval.CqlOps` with matching arity;
  *  - any `DECIMAL` in a declared type is `DECIMAL(38,18)` (the `java.math.BigDecimal` encoder precision —
  *    a mismatch here fails UC binding, `EXEC-UC-STRUCT-ARG`);
  *  - the manifest covers the ENTIRE catalog (every UDF is UC-ready), and no `cql_choice_nav_*`
  *    UDF is in the catalog (choice nav is native, `EMIT-SQL-FLAT-CHOICE`);
  *  - `toJson` emits well-formed, non-empty JSON.
  * Pure JVM (no Spark session), so it runs in the compiler suite without the Connect server. */
@RunWith(classOf[JUnitRunner])
class CqlUcManifestSpec extends AnyFunSuite {

  private val handlerClass = "com.databricks.cql.runtime.eval.CqlOps"

  test("every manifest sqlName is a real catalog function name (EXEC-UC-MANIFEST)") {
    val catalogNames = CqlRuntimeUdfs.catalog.keySet
    UcFunctionManifest.entries.foreach { e =>
      assert(catalogNames.contains(e.sqlName), s"manifest lists ${e.sqlName}, absent from CqlRuntimeUdfs.catalog")
    }
  }

  test("every handler FQN resolves to a real CqlOps method with matching arity (EXEC-UC-MANIFEST)") {
    val cls = Class.forName(handlerClass)
    UcFunctionManifest.entries.foreach { e =>
      assert(e.handler.startsWith(handlerClass + "."), s"${e.sqlName}: handler not on $handlerClass: ${e.handler}")
      val method = e.handler.stripPrefix(handlerClass + ".")
      val overloads = cls.getMethods.filter(_.getName == method)
      assert(overloads.nonEmpty, s"${e.sqlName}: no method $method on $handlerClass")
      assert(overloads.exists(_.getParameterCount == e.argTypes.size),
        s"${e.sqlName}: no $method overload with ${e.argTypes.size} params (found arities ${overloads.map(_.getParameterCount).mkString(",")})")
    }
  }

  test("any DECIMAL in a declared type is DECIMAL(38,18) (EXEC-UC-STRUCT-ARG precision rule)") {
    val badDecimal = "(?i)DECIMAL\\((?!38,\\s*18\\))".r
    UcFunctionManifest.entries.foreach { e =>
      (e.argTypes :+ e.returnType).foreach { t =>
        assert(badDecimal.findFirstIn(t).isEmpty,
          s"${e.sqlName}: DECIMAL in '$t' must be DECIMAL(38,18) to match the java.math.BigDecimal encoder")
      }
    }
  }

  test("the manifest covers the ENTIRE catalog (every UDF is UC-ready)") {
    val manifestNames = UcFunctionManifest.entries.map(_.sqlName).toSet
    assert(manifestNames == CqlRuntimeUdfs.catalog.keySet,
      s"manifest vs catalog mismatch: only in manifest=${manifestNames -- CqlRuntimeUdfs.catalog.keySet}, " +
        s"only in catalog=${CqlRuntimeUdfs.catalog.keySet -- manifestNames}")
  }

  test("the retired cql_choice_nav_* UDFs are gone from the catalog (EMIT-SQL-FLAT-CHOICE)") {
    // Choice navigation lowers natively (flat demand-projection); no UDF is needed.
    CqlRuntimeUdfs.catalog.keySet.foreach(n =>
      assert(!n.startsWith("cql_choice_nav"), s"$n should have been retired (native flat choice nav)"))
  }

  test("toJson is non-empty and structurally well-formed (EXEC-UC-MANIFEST)") {
    val json = UcFunctionManifest.toJson
    assert(json.startsWith("[") && json.endsWith("]"))
    assert(json.count(_ == '{') == UcFunctionManifest.entries.size)
    assert(json.count(_ == '{') == json.count(_ == '}'))
    UcFunctionManifest.entries.foreach(e => assert(json.contains("\"" + e.sqlName + "\"")))
  }
}
