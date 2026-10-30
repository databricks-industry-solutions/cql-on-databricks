package com.databricks.cql.it

import com.databricks.cql.translate.CqlToElmCli
import org.cqframework.cql.cql2elm.{CqlCompilerException, CqlTranslator, LibraryManager, ModelManager,
  StringLibrarySourceProvider}
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

import scala.jdk.CollectionConverters._

/** LANG-NO-IMPLICIT-INCLUDE — the compiler translates CQL exactly as written, so it accepts a library exactly
  * when the reference translator (cqframework `cql-to-elm`) does. Differential: each case is translated by
  * `CqlToElmCli.translateText` and by a bare reference `CqlTranslator` given the same library sources
  * (the corpus's FHIRHelpers), and the two must agree.
  *
  * The cases are the ones the removed `FhirHelpersSource.inject` changed: a FHIR conversion with no
  * `include FHIRHelpers` (accepted here, refused by the reference), and an unnamed library (refused here,
  * because the inserted include is one the reference forbids an unnamed library: `LibraryBuilder.addInclude`,
  * "Unnamed libraries cannot reference other libraries"). Spec: CQL 2.0.0 Developer's Guide, Data Models
  * ("To reference the data types in a data model, an appropriate local using declaration must be
  * specified") and Libraries ("the library declaration is optional in a CQL document"). */
@RunWith(classOf[JUnitRunner])
final class NoImplicitIncludeSpec extends AnyFunSuite {

  private val fhirHelpers = Paths.read(Paths.cql("FHIRHelpers"))

  private def reference(cql: String): Seq[String] = {
    val libraryManager = new LibraryManager(new ModelManager())
    libraryManager.getLibrarySourceLoader.registerProvider(new StringLibrarySourceProvider(Seq(fhirHelpers).asJava))
    CqlTranslator.fromText(cql, libraryManager).getErrors.asScala.toSeq
      .filter(_.getSeverity == CqlCompilerException.ErrorSeverity.Error).map(_.getMessage)
  }

  private def ours(cql: String): Seq[String] = CqlToElmCli.translateText(cql, Seq(fhirHelpers)).errors

  /** Both sides agree, and agree with `accepted`. */
  private def agree(cql: String, accepted: Boolean): Unit = {
    val (r, o) = (reference(cql), ours(cql))
    assert(r.isEmpty == accepted, s"the reference translator ${if (accepted) "refused" else "accepted"} it: $r")
    assert(o.isEmpty == r.isEmpty, s"ours ${if (o.isEmpty) "accepted" else "refused"} what the reference did not: $o")
  }

  private val conversion = "define \"Born\": Patient.birthDate < @2026-01-01\n"

  test("a FHIR conversion with no FHIRHelpers include is refused, as the reference refuses it") {
    agree(s"library NoInclude version '1.0.0'\nusing FHIR version '4.0.1'\ncontext Patient\n$conversion",
      accepted = false)
  }

  test("the same library that includes FHIRHelpers is accepted") {
    agree(s"library WithInclude version '1.0.0'\nusing FHIR version '4.0.1'\n" +
      s"include FHIRHelpers version '4.0.1' called FHIRHelpers\ncontext Patient\n$conversion", accepted = true)
  }

  test("an unnamed FHIR library that needs no conversion is accepted (it was refused before)") {
    agree("using FHIR version '4.0.1'\ncontext Patient\ndefine \"Has Encounter\": exists [Encounter]\n",
      accepted = true)
  }

  test("an unnamed library that includes one is refused, as the reference refuses it") {
    agree("using FHIR version '4.0.1'\ninclude FHIRHelpers version '4.0.1' called FHIRHelpers\n" +
      s"context Patient\n$conversion", accepted = false)
  }

  test("a model-free unnamed library is accepted") {
    agree("define \"Two\": 2 + 2\n", accepted = true)
  }

  // LANG-FHIRHELPERS-FROM-CONTENT: only the supplied sources resolve an include. The `org.cqframework:quick` jar
  // on this classpath registers a provider that serves FHIRHelpers 1.0.2 to 4.0.1 (a bare reference translator
  // with no sources accepts `include FHIRHelpers version '4.0.1'` through it); ours must never reach it.
  private def withInclude(version: String): String =
    s"library WithInclude version '1.0.0'\nusing FHIR version '4.0.1'\n" +
      s"include FHIRHelpers version '$version' called FHIRHelpers\ncontext Patient\n$conversion"

  test("an included FHIRHelpers that no source supplies fails, though the quick jar ships that version") {
    val errors = CqlToElmCli.translateText(withInclude("4.0.1")).errors
    assert(errors.exists(_.contains("Library FHIRHelpers version '4.0.1' is not among the supplied library sources")),
      s"expected the include to fail as not supplied, got: $errors")
  }

  test("an included FHIRHelpers version that no supplied source declares fails") {
    val errors = CqlToElmCli.translateText(withInclude("4.0.2"), Seq(fhirHelpers)).errors
    assert(errors.exists(_.contains("Library FHIRHelpers version '4.0.2' is not among the supplied library sources")),
      s"expected the include to fail as not supplied, got: $errors")
  }
}
