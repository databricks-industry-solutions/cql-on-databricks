package com.databricks.cql.it

import com.databricks.cql.env.SourceBinding
import com.databricks.cql.service.{CompileRequest, CompileService, DataSourceRoutines}
import com.databricks.cql.translate.CqlToElmCli
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** SVC-COMPILE-BATCH, RUN-CLIENT-RESULT-IDENTITY — whole-plan parity on every corpus measure
  * ([[Corpus.measureDefs]]), no data execution or durable fixtures. */
@RunWith(classOf[JUnitRunner])
final class BatchCompileSpec extends AnyFunSuite {
  Corpus.measureDefs.foreach { md =>
    test(s"SVC-COMPILE-BATCH: both measurement periods preserve every ${md.library} SQL recipe and durable identity") {
      val root = Paths.read(Paths.cql(md.library))
      val deps = md.deps.map(d => Paths.read(Paths.cql(d)))
      def elm(source: String): String = {
        val result = CqlToElmCli.translateText(source, deps)
        assert(result.errors.isEmpty, result.errors.mkString("; "))
        result.elmJson.get
      }
      val rootElm = elm(root)
      val depElms = deps.map(elm)
      val types = md.resourceTypes
      val dataSource = DataSourceRoutines("synthetic", "source", types.map(t => "retrieve_" + t.toLowerCase))
      val recipeCounts = for (year <- Seq(2025, 2026)) yield {
        val request = CompileRequest(cql = Some(root), depCql = deps, durable = true,
          measurementPeriod = Some(s"$year-01-01" -> s"$year-12-31"), dataSource = Some(dataSource),
          terminologyTable = Some("synthetic.runtime.valuesets"), udfSchema = Some("synthetic.runtime"))
        val legacy = request.copy(cql = None, elmJson = rootElm, depCql = Seq.empty, deps = depElms,
          measurementPeriod = None,
          parameters = Map("Measurement Period" -> s"Interval[@$year-01-01T00:00:00.000, @$year-12-31T23:59:59.999]"))
        val batch = CompileService.compilePipeline(request, hasTerminology = true)
        val previous = CompileService.compilePipeline(legacy, hasTerminology = true)
        assert(batch == previous, s"${md.library} CY$year bundled compile changed SQL or identities")
        // RUN-CLIENT-RESULT-IDENTITY: explicit bindings name no data source, so a durable compile refuses them.
        val explicit = legacy.copy(dataSource = None,
          sources = types.map(t => t -> SourceBinding("retrieve_" + t, Some("synthetic.source"))).toMap)
        intercept[IllegalArgumentException] { CompileService.compilePipeline(explicit, hasTerminology = true) }
        batch.datasets.size
      }
      // One recipe per define the population slice keeps, so the period never changes the count.
      assert(recipeCounts.distinct.size == 1, s"recipe count differs by period: $recipeCounts")
      assert(recipeCounts.head >= md.populations.size, s"fewer recipes than populations: $recipeCounts")
    }
  }
}
