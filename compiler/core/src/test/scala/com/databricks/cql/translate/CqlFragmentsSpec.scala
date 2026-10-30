package com.databricks.cql.translate

import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** L1 (CONF-TEST-LAYERS): SVC-NOTEBOOK-SESSION-SCOPE — header-less fragments merge into one library that the
  * real translator accepts, the latest declaration of a name winning.
  *
  * @RunWith(JUnitRunner) is required for discovery under the JUnit Platform. */
@RunWith(classOf[JUnitRunner])
class CqlFragmentsSpec extends AnyFunSuite {

  private def elm(cql: String): ujson.Value = {
    val result = CqlToElmCli.translateText(cql)
    assert(result.errors.isEmpty, result.errors.mkString("; "))
    ujson.read(result.elmJson.get)("library")
  }

  private def defs(cql: String): Map[String, ujson.Value] =
    elm(cql)("statements")("def").arr.map(d => d("name").str -> d).toMap

  test("SVC-NOTEBOOK-SESSION-SCOPE: a later fragment sees an earlier define unqualified") {
    val merged = CqlFragments.merge(Seq("define \"foo\": 1", "define \"bar\": \"foo\" + 1"))
    assert(merged.defines == Seq("bar"))
    assert(defs(merged.cql).keySet == Set("foo", "bar"))
  }

  test("SVC-NOTEBOOK-SESSION-SCOPE: redeclaring a define replaces it") {
    val merged = CqlFragments.merge(Seq("define \"foo\": 'bar'", "define \"foo\": 'baz'"))
    assert(merged.defines == Seq("foo"))
    assert(defs(merged.cql)("foo")("expression")("value").str == "baz")
  }

  test("SVC-NOTEBOOK-SESSION-SCOPE: a redeclared parameter, valueset or include replaces by name or alias") {
    val merged = CqlFragments.merge(Seq(
      "parameter \"Limit\" Integer default 1\nvalueset \"VS\": 'urn:one'",
      "parameter \"Limit\" Integer default 2\nvalueset \"VS\": 'urn:two'\ndefine \"L\": \"Limit\""))
    val lib = elm(merged.cql)
    assert(lib("parameters")("def").arr.map(_("default")("value").str) == Seq("2"))
    assert(lib("valueSets")("def").arr.map(_("id").str) == Seq("urn:two"))
  }

  test("SVC-NOTEBOOK-SESSION-SCOPE: a name redeclared as another kind moves to it") {
    val merged = CqlFragments.merge(Seq("valueset \"X\": 'urn:x'", "define \"X\": 1"))
    val lib = elm(merged.cql)
    assert(!lib.obj.contains("valueSets"))
    assert(defs(merged.cql).contains("X"))
  }

  test("SVC-NOTEBOOK-SESSION-SCOPE: an identical using is one declaration; a second model version fails as on disk") {
    val same = CqlFragments.merge(Seq("using FHIR version '4.0.1'\ndefine \"a\": 1",
                                      "using FHIR version '4.0.1'\ndefine \"b\": 2"))
    assert("using FHIR".r.findAllIn(same.cql).size == 1)
    assert(defs(same.cql).keySet == Set("a", "b"))

    val clash = CqlFragments.merge(Seq("using FHIR version '4.0.1'", "using FHIR version '4.0.0'"))
    val errors = CqlToElmCli.translateText(clash.cql).errors
    assert(errors.exists(_.contains("because version 4.0.1 is already loaded")), errors)
  }

  test("SVC-NOTEBOOK-SESSION-SCOPE: each fragment starts in the default context") {
    val merged = CqlFragments.merge(Seq(
      "using FHIR version '4.0.1'\ncontext Patient\ndefine \"p\": 1",
      "define \"u\": 2"))
    val byName = defs(merged.cql)
    assert(byName("p")("context").str == "Patient")
    assert(byName("u")("context").str == "Unfiltered")
  }

  test("SVC-NOTEBOOK-SESSION-SCOPE: overloads of a function coexist; the same signature replaces") {
    val merged = CqlFragments.merge(Seq(
      "define function \"F\"(x Integer): x\ndefine function \"F\"(x String): x",
      "define function \"F\"(x Integer): x + 1"))
    val functions = elm(merged.cql)("statements")("def").arr.filter(_("type").str == "FunctionDef")
    assert(functions.size == 2)
    assert(merged.defines.isEmpty)
  }

  test("SVC-NOTEBOOK-SESSION-SCOPE: a fragment with a library header is refused") {
    val error = intercept[IllegalArgumentException] {
      CqlFragments.merge(Seq("library Named version '1.0.0'\ndefine \"a\": 1"))
    }
    assert(error.getMessage.contains("library Named"))
  }

  test("SVC-NOTEBOOK-SESSION-SCOPE: a syntax error fails loudly instead of merging a recovered parse") {
    val error = intercept[IllegalArgumentException](CqlFragments.merge(Seq("define \"a\": 1", "define \"b\" 2")))
    assert(error.getMessage.startsWith("fragment 2 is not valid CQL"))
  }
}
