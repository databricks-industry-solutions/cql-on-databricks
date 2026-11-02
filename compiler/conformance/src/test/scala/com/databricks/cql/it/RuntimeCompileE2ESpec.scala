package com.databricks.cql.it

import com.databricks.cql.env.Context
import com.databricks.cql.retrieve.{DataProvider, TerminologyProvider}
import com.databricks.cql.service.{CompileService, OssCompileHost}
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

import java.io.File

/** The LIVE, end-to-end runtime-compile gate (PHASE-10, `BUILD-CLIENT` / `BUILD-COMPILE-SERVICE`,
  * `CONF-GATE-SELFDIFF`): drive the WHOLE OSS loop and self-diff its result against the in-JVM
  * direct-execute truth.
  *
  * Per fixture:
  *   1. Execute the library IN-JVM via the SQL back end ([[SqlDifferential.engineResults]]) and collect
  *      the per-(subject, define) result — the TRUTH the client must reproduce.
  *   2. Front that same [[CompileService]] with the default [[OssCompileHost]] over HTTP (in-process,
  *      as [[CompileHostSpec]] does).
  *   3. Run the Python `client` end-to-end driver as a SEPARATE process on the SQL SUBSTRATE
  *      (the deploy path, PIPE-RUNTIME-COMPILE/EXEC-SQL-BACKEND): `load_library(elm)` →
  *      `cql(target, format="sql")` through a `RemoteProvider` (HTTP `/compile-sql`) → run the compiled SQL
  *      script via `spark.sql` in ITS OWN session on the SAME Connect server → the result printed as JSON.
  *   4. Assert the client's per-(subject, define) result EQUALS the truth.
  *
  * This proves the client + HTTP transport + the SQL runtime-compile path are faithful — the SAME emitter
  * the primary conformance gate ([[SqlDifferentialSpec]]) validates, now driven end-to-end through the
  * client (test-what-you-ship). Lowering CORRECTNESS itself is gated elsewhere ([[SqlDifferentialSpec]]),
  * so any divergence here is a client/transport fault — the exact surface this gate exists to catch.
  *
  * Data-free only: a FHIR case needs the driver to bind its retrieves (`BIND-CALL-SITE`) to relations the
  * driver's own session can read, which it does not do yet. [[SqlDifferentialSpec]] binds every FHIR case.
  *
  * Skips cleanly (never fails) unless BOTH a Connect server is reachable AND `-Dcqldatabricks.pyClient=<python>`
  * names a Python with pyspark + the `cql_databricks` package importable, mirroring the
  * `-Dcqldatabricks.requireConnect` convention so the suite stays green on a host without a configured
  * pyspark client. */
@RunWith(classOf[JUnitRunner])
final class RuntimeCompileE2ESpec extends AnyFunSuite {

  /** The Python interpreter (venv) that runs the client driver; the whole gate skips without it. */
  private val pyClient: Option[String] =
    sys.props.get("cqldatabricks.pyClient").map(_.trim).filter(_.nonEmpty)

  /** repo-root/compiler/client — resolved off `Paths.rootDir`, so the driver + package are found
    * regardless of the test worker's CWD and independent of where the corpus lives. */
  private val clientDir: File = new File(Paths.rootDir, "compiler/client")
  private val driver: File    = new File(clientDir, "scripts/e2e_run.py")

  /** A data-free scalar fixture (no adapters) — the purest locally-hostable case. */
  test("Arithmetic: runtime-compiled cql() self-diffs clean vs direct-execute (CONF-GATE-SELFDIFF)") {
    runCase("Arithmetic", Context.Unfiltered, data = None, terminology = None)
  }

  private def runCase(fixture: String, context: Context,
                      data: Option[DataProvider], terminology: Option[TerminologyProvider]): Unit = {
    val py = pyClient.getOrElse(
      cancel("client E2E skipped: set -Dcqldatabricks.pyClient=<python-with-pyspark+cql_databricks>"))
    assert(driver.exists(), s"the E2E driver is missing at ${driver.getAbsolutePath}")

    val spark   = SparkConnect.session() // a live server, or a clean skip
    val elmFile = Paths.elm(fixture)
    val c       = Differential.Case(fixture, context, data, terminology)

    // (1) TRUTH — the JVM SQL execution the client (compiling+running the SAME SQL via the service in its
    // own session) must reproduce. Results are back-end/config-agnostic (grain-first + materialization are
    // result-preserving), so this oracle matches the client's compile regardless of config knobs.
    val truth: Map[String, Map[String, ujson.Value]] = SqlDifferential.engineResults(spark, c)
    val targets   = truth.headOption.map(_._2.keys.toSeq).getOrElse(Nil)

    // (2) The SAME service, fronted by the local host over HTTP (in-process).
    val host = new OssCompileHost(new CompileService(terminology)).start()
    try {
      // (3) Run the client E2E driver as a separate process (its own Connect session) on the SQL substrate
      // (test-what-you-ship): mount the runtime contract, then compile+execute each define via its own
      // cql("define", format="sql") call — the faithful deploy-path client usage.
      val client = runClient(py, host.endpoint, SparkConnect.remote, elmFile, context, targets)

      // (4) Self-diff: identical subjects, identical defines, identical values.
      assert(client.obj.keySet == truth.keySet,
        s"[$fixture] subject sets differ: client=${client.obj.keySet} truth=${truth.keySet}")
      truth.foreach { case (subject, defines) =>
        val clientDefs = client(subject).obj
        assert(clientDefs.keySet == defines.keySet,
          s"[$fixture] subject '$subject' define sets differ: client=${clientDefs.keySet} truth=${defines.keySet}")
        defines.foreach { case (name, value) =>
          assert(clientDefs(name) == value,
            s"[$fixture] subject '$subject' define '$name': client=${clientDefs(name)} truth=$value")
        }
      }
    } finally host.stop()
  }

  /** Invoke `scripts/e2e_run.py` on the SQL substrate (one `cql("define", format="sql")` per define in
    * `targets`) and parse its stdout JSON as `{subject -> {define -> value}}`.
    * Fails loudly with the captured stderr on a non-zero exit. */
  private def runClient(python: String, endpoint: String, remote: String,
                        elmFile: File, context: Context, targets: Seq[String]): ujson.Value = {
    val args = Seq(
      python, driver.getAbsolutePath,
      "--endpoint", endpoint,
      "--remote", remote,
      "--elm", elmFile.getAbsolutePath,
      "--context", context.name,
      "--targets", targets.mkString(","))
    val pb = new ProcessBuilder(args: _*)
    pb.directory(clientDir)                       // so `PYTHONPATH=.` finds the cql_databricks package
    pb.environment().put("PYTHONPATH", ".")
    pb.redirectErrorStream(false)
    val proc = pb.start()
    val out  = new String(proc.getInputStream.readAllBytes(), java.nio.charset.StandardCharsets.UTF_8)
    val err  = new String(proc.getErrorStream.readAllBytes(), java.nio.charset.StandardCharsets.UTF_8)
    val code = proc.waitFor()
    assert(code == 0, s"client driver failed (exit $code):\n$err\n--- stdout ---\n$out")
    ujson.read(out)
  }
}
