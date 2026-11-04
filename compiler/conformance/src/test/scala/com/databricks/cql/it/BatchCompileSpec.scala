package com.databricks.cql.it

import com.databricks.cql.env.{DataBinding, RetrieveRelation, TerminologyBinding}
import com.databricks.cql.service.{CompileRequest, CompileService}
import com.databricks.cql.translate.CqlToElmCli
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** SVC-COMPILE-BATCH, CLIENT-RESOLVER-IDENTITY — whole-plan parity on every corpus measure
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
      val recipeCounts = for (year <- Seq(2025, 2026)) yield {
        val request = CompileRequest(cql = Some(root), depCql = deps, durable = true,
          measurementPeriod = Some(s"$year-01-01" -> s"$year-12-31"),
          data = Some(DataBinding(Some(DataBinding.Rule("synthetic.data", Some("synthetic.data"))), Map.empty, StockShapes.all)),
          terminology = Some(TerminologyBinding(Some(Seq(RetrieveRelation("synthetic.terminology.valueset",
            Some("synthetic.terminology")))), Map.empty)), udfSchema = Some("synthetic.runtime"))
        val legacy = request.copy(cql = None, elmJson = rootElm, depCql = Seq.empty, deps = depElms,
          measurementPeriod = None,
          parameters = Map("Measurement Period" -> s"Interval[@$year-01-01T00:00:00.000, @$year-12-31T23:59:59.999]"))
        val batch = CompileService.compilePipeline(request)
        val previous = CompileService.compilePipeline(legacy)
        assert(batch == previous, s"${md.library} CY$year bundled compile changed SQL or identities")
        // BIND-CALL-SITE: a durable compile that retrieves and binds no data is refused.
        intercept[com.databricks.cql.error.UnsupportedNode] { CompileService.compilePipeline(legacy.copy(data = None)) }
        batch.datasets.size
      }
      // One recipe per define the population slice keeps, so the period never changes the count.
      assert(recipeCounts.distinct.size == 1, s"recipe count differs by period: $recipeCounts")
      assert(recipeCounts.head >= md.populations.size, s"fewer recipes than populations: $recipeCounts")
    }
  }
}
