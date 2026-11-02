package com.databricks.cql.it

import com.databricks.cql.env.{DataBinding, RetrieveRelation, TerminologyBinding}
import com.databricks.cql.service.{CompileRequest, CompileService, DatasetRecipe, PipelinePlan}
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** EMIT-ROOT-INDEPENDENT, CLIENT-INCLUDED-REUSE — every corpus measure ([[Corpus.measureDefs]]) compiled alone and
  * inside a synthetic library that includes it gives each of its defines the same recipe: name, SQL, reads, origin
  * and fingerprint. So the including library reuses the measure's stored results (DEC-LIBRARY-OWNS-RESULTS). Pure
  * compile, no Spark. */
@RunWith(classOf[JUnitRunner])
final class IncludedReuseSpec extends AnyFunSuite {
  private val Wrapper = "SyntheticReleaseCheck"

  Corpus.measureDefs.foreach { md =>
    val measure = Paths.read(Paths.cql(md.library))
    val deps = md.deps.map(d => Paths.read(Paths.cql(d)))
    val model = measure.linesIterator.map(_.trim).find(_.startsWith("using "))
      .getOrElse(fail(s"${md.library} declares no data model"))

    /** A synthetic library that includes the measure, re-exports each population and adds one define of its own
      * (CLIENT-RELEASE-TESTS). */
    val reexports = md.populations.zipWithIndex.map { case ((define, _), i) => s"Population $i" -> define }
    val wrapper =
      (Seq(s"library $Wrapper version '0.1.0'", model,
        "include FHIRHelpers version '4.0.1' called FHIRHelpers",
        s"include ${md.library} version '${md.version}' called Measure",
        "parameter \"Measurement Period\" Interval<DateTime>",
        "context Patient") ++
        reexports.map { case (own, define) => s"define \"$own\": Measure.\"$define\"" } ++
        Seq("define \"Every Population\": " + reexports.map { case (own, _) => s"\"$own\"" }.mkString(" and ")))
        .mkString("\n")

    def compile(root: String, closure: Seq[String]): PipelinePlan =
      CompileService.compilePipeline(CompileRequest(cql = Some(root), depCql = closure, durable = true,
        measurementPeriod = Some("2026-01-01" -> "2026-12-31"),
        data = Some(DataBinding(Some(DataBinding.Rule("synthetic.data", Some("synthetic.data"))), Map.empty)),
        terminology = Some(TerminologyBinding(Some(Seq(RetrieveRelation("synthetic.terminology.valueset",
          Some("synthetic.terminology")))), Map.empty)), udfSchema = Some("synthetic.runtime")))

    lazy val alone = compile(measure, deps)
    lazy val included = compile(wrapper, measure +: deps)

    def byName(plan: PipelinePlan): Map[String, DatasetRecipe] = plan.datasets.map(r => r.name -> r).toMap

    test(s"EMIT-ROOT-INDEPENDENT: every ${md.library} define has the same recipe alone and included") {
      val inside = byName(included)
      info(s"${md.library} alone: ${alone.datasets.size} recipes; inside $Wrapper: ${included.datasets.size} " +
        s"(${included.datasets.count(_.library == Wrapper)} of its own)")
      alone.datasets.foreach { recipe =>
        val other = inside.getOrElse(recipe.name, fail(s"${recipe.name} (${recipe.library}.${recipe.define}) is not a " +
          s"recipe inside the including library; it has ${inside.keys.toSeq.sorted.mkString(", ")}"))
        assert(other.query == recipe.query, s"${recipe.name}: SQL differs")
        assert(other.reads == recipe.reads, s"${recipe.name}: reads differ")
        assert((other.library, other.libraryVersion, other.define) == (recipe.library, recipe.libraryVersion, recipe.define))
        assert(other.durable.map(_.fingerprint) == recipe.durable.map(_.fingerprint), s"${recipe.name}: fingerprint differs")
      }
    }

    test(s"EMIT-ROOT-INDEPENDENT: ${md.library} recipes are named and attributed by their owning library") {
      val prefix = s"${md.library.toLowerCase}_${md.version.replace('.', '_')}__"
      md.populations.foreach { case (define, _) =>
        val recipe = alone.datasets.find(_.define == define).getOrElse(fail(s"no $define recipe"))
        assert(recipe.name.startsWith(prefix), s"${recipe.name} does not start with $prefix")
        assert(recipe.library == md.library && recipe.libraryVersion == md.version)
      }
      val own = included.datasets.filter(_.library == Wrapper).map(_.define).toSet
      assert(own == (reexports.map(_._1) :+ "Every Population").toSet)
      assert(included.datasets.filter(_.library == Wrapper).forall(_.name.startsWith(s"${Wrapper.toLowerCase}_0_1_0__")))
    }

    test(s"EMIT-ROOT-INDEPENDENT: no ${md.library} recipe names the library being compiled in its SQL") {
      (alone.datasets ++ included.datasets).foreach { recipe =>
        assert(!recipe.query.contains("named_struct('id'"), s"${recipe.name} passes a library argument")
      }
      included.datasets.filter(_.library != Wrapper).foreach { recipe =>
        assert(!recipe.query.toLowerCase.contains(Wrapper.toLowerCase), s"${recipe.name} names the including library")
      }
    }
  }
}
