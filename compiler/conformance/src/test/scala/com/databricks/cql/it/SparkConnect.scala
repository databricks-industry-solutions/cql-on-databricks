package com.databricks.cql.it

import com.databricks.cql.runtime.udf.CqlRuntimeUdfs
import org.apache.spark.sql.SparkSession
import org.apache.spark.sql.internal.ConnectStacktrace
import org.scalatest.exceptions.TestCanceledException

import java.net.{InetSocketAddress, Socket, URI}

/** The single seam through which the conformance suite obtains a Spark Connect session
  * (TECH-LOWERING-TARGET, CONF-GATE-MEASURE). The test JVM is a pure Connect CLIENT: it
  * connects to whatever `cqldatabricks.sparkRemote` names — a local OSS Connect server for the
  * fast loop (`./gradlew :compiler:conformance:connectServer`), or a Databricks Connect instance
  * (same suite, different URL). When no server is reachable, plan-executing tests CANCEL
  * (skip) rather than fail, so the pure-compiler suite stays green with no Spark running. */
object SparkConnect {

  val remote: String = sys.props.getOrElse("cqldatabricks.sparkRemote", "sc://localhost:15002")

  /** CONF-GATES: in CI the differential/self-diff gates are *required* status checks, so an
    * unreachable endpoint must be a hard FAILURE, not a clean skip — otherwise a server that
    * failed to launch would let every plan-executing gate cancel (green) and silently void the
    * gate. Set `-Dcqldatabricks.requireConnect=true` (the CI gate job does) to turn "no server" into a
    * test failure; the default (false) keeps the local pure-compiler loop green with no Spark. */
  val requireConnect: Boolean = sys.props.get("cqldatabricks.requireConnect").contains("true")

  /** A connected session, or a skip if the endpoint is unreachable. Memoized: the gRPC
    * channel is cheap to share and the server holds session state per client.
    *
    * NB: the Spark Connect client retries a refused connection in `GrpcRetryHandler` with
    * minutes of backoff, so we do NOT learn "no server" fast from `getOrCreate()`. A cheap TCP
    * probe up front turns "no local server" into an immediate skip instead of a long hang. */
  private lazy val sessionOrSkip: Either[String, SparkSession] = {
    val hint =
      s"Start a local server with `./gradlew :compiler:conformance:connectServer`, or set " +
        s"-Dcqldatabricks.sparkRemote=sc://<databricks-host> (see docs/deploy.md)."
    if (!endpointListening(remote))
      Left(s"Spark Connect endpoint '$remote' not listening. $hint")
    else
      try {
        // Disable the Connect client's per-operation JVM stack-trace capture (phase-8): every proto
        // Expression/Relation otherwise carries a `common.origin.jvm_origin.stack_trace` subtree, which
        // is ~80% of the serialized plan BYTES (and counts against the 128 MB gRPC limit). It is pure
        // debug metadata — the `LoweredTree` render ignores it (it walks only relation nodes), so
        // dropping it changes neither results nor the lowered-tree golden, only the wire size. This is COMPLEMENTARY to the
        // structural operand-materialization fix (which removes duplicated NODES), never a substitute.
        // The toggle (`spark.sql.dataFrameQueryContext.enabled`) is read CLIENT-side from the local
        // `SqlApiConf`, NOT the remote session, so a builder `.config` does nothing on a pure Connect
        // client; we install a conf getter that turns the capture off (see ConnectStacktrace).
        ConnectStacktrace.disableQueryContextCapture()
        val spark = SparkSession.builder().remote(remote).getOrCreate()
        // Disable the Connect server-side PLAN CACHE for this (shared, memoized) session. The cache keys
        // an analyzed LogicalPlan by the unresolved relation proto, but our staging reuses DETERMINISTIC
        // temp-view names (`Materializer.viewName` → `__mat_<define-key>`). When the SAME fixture is staged
        // by more than one spec in this JVM (a measure in the differential AND measure-score gates)
        // — or the same define is staged boxed-vs-grain-first in a self-diff — a later `createOrReplaceTempView`
        // rebinds `__mat_X` to DIFFERENT content, yet `spark.table("__mat_X")` hits the cached analyzed plan
        // of the EARLIER content. That is the cross-test flake (a stale Choice-union plan surfacing under an
        // unrelated test as `FIELD_NOT_FOUND __choice`). The cache is a pure optimization; off, every relation
        // re-analyzes against the CURRENT catalog, so each test sees only its own staged views. Best-effort:
        // tolerate a Spark build that doesn't expose the conf.
        try spark.conf.set("spark.connect.session.planCache.enabled", "false")
        catch { case _: Throwable => () }
        spark.range(0, 1).count() // force a round-trip so a half-open endpoint fails here
        installUdfCatalog(spark)
        Right(spark)
      } catch {
        case e: Throwable =>
          Left(s"Spark Connect endpoint '$remote' not reachable (${e.getClass.getSimpleName}: ${e.getMessage}). $hint")
      }
  }

  /** EXEC-UDF-LIBRARY (compiler/docs/execution-model.md): ship the opaque-UDF catalog JAR to the
    * Connect server (`addArtifact`) and register the catalog by name — ONCE per session, wired into this
    * seam (not per query), exactly where the recipe (UdfFeasibilitySpec) proved it. The JAR path comes
    * from the build (`cqldatabricks.udfJar`); absent (or a Spark that can't accept it) it is a best-effort
    * no-op so the emitted SQL simply must not reference a `cql_*` UDF yet — which is true until Phase 2
    * turns the emit on. Failures are surfaced (not swallowed) once emit depends on it. */
  private def installUdfCatalog(spark: SparkSession): Unit =
    sys.props.get("cqldatabricks.udfJar").filter(_.nonEmpty).foreach { jar =>
      CqlRuntimeUdfs.install(spark, jar)
    }

  /** Fast TCP liveness probe of an `sc://host:port` endpoint (2s). Non-sc remotes (none today)
    * assume reachable and let `getOrCreate()` decide. */
  private def endpointListening(r: String): Boolean = {
    val hostPort =
      try {
        val u = URI.create(r.replaceFirst("^sc://", "http://"))
        if (u.getHost == null) None else Some((u.getHost, if (u.getPort > 0) u.getPort else 15002))
      } catch { case _: Throwable => None }
    hostPort match {
      case None => true
      case Some((host, port)) =>
        val s = new Socket()
        try { s.connect(new InetSocketAddress(host, port), 2000); true }
        catch { case _: Throwable => false }
        finally s.close()
    }
  }

  /** A live session, or — depending on `requireConnect` (CONF-GATES) — either FAIL the gate (CI:
    * the server is supposed to be up, so a miss is a real failure) or cancel/skip it (local: keep
    * the pure-compiler loop green with no Spark running). */
  def session(): SparkSession = sessionOrSkip match {
    case Right(s)              => s
    case Left(why) if requireConnect => throw new RuntimeException(s"Spark Connect required (cqldatabricks.requireConnect=true) but unavailable: $why")
    case Left(why)             => throw new TestCanceledException(why, 0)
  }
}
