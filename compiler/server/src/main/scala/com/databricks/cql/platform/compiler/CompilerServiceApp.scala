package com.databricks.cql.platform.compiler

import com.sun.net.httpserver.{HttpExchange, HttpHandler, HttpServer}
import com.databricks.cql.meta.LibraryGraph
import com.databricks.cql.passes.BuildInfo
import com.databricks.cql.retrieve.{ResourceStruct, TerminologyProvider}
import com.databricks.cql.sema.{FhirModel, FhirSchema}
import com.databricks.cql.service.{CompileRequest, CompileService, DefineShape, MergeCql, SqlStatementOut}
import com.databricks.cql.translate.CqlToElmCli

import java.net.InetSocketAddress
import java.nio.charset.StandardCharsets
import java.util.concurrent.Executors

/** The CQL Compiler Service — the accelerator's PLATFORM HOST over the engine's compile service
  * (`SVC-COMPILE-ENDPOINT` / `SVC-API-HOST`, `compiler/docs/compiler-service.md`).
  *
  * One JVM endpoint serving the engine's compile ops over HTTPS-shaped REST/JSON (`SVC-PROTOCOL`),
  * reconciled to the engine's **SQL-script backend** (`EXEC-SQL-BACKEND`, 2026-07 — supersedes the
  * plan-proto model):
  *   - authoring:  POST /translate       {cql, includes?}                 -> {elm, diagnostics, ok}
  *   - runtime:    POST /compile-sql      {elm|cql, target?, boundaries?}  -> {statements:[{name,kind,sql}], target, defines, shapes}
  *   - pipeline:   POST /compile-pipeline {elm|cql, boundaries?, ...}      -> {datasets:[{name,target,reads,query,statements}]}
  *   - SDP graph:  POST /describe         {elm|cql, context?, ...}         -> LibraryGraph JSON
  *   - mount ddl:  POST /resource-schema  {types:[...]}                    -> {schemas:{Type:{modeled,patientContext,ddl}}}
  *   - modeled set:POST /resource-schema  {} (empty/absent types)          -> {resourceTypes:[...sorted...]}
  *   - liveness:   GET  /health                                           -> {status:"ok"}
  *   - discovery:  GET  /capabilities                                     -> {name, version, protocol, methods}
  *
  * This mirrors the engine's own reference host (`OssCompileHost`, `service`) — the wire
  * contract is IDENTICAL, so the `cql-databricks` `RemoteProvider` points at either host — and adds the
  * two platform concerns the engine deliberately omits (`BUILD-COMPILE-SERVICE`): a PUBLIC bind (a
  * Databricks App is reached from serverless SDP, not loopback) and AUTH (RFC 6750 bearer; OAuth token
  * exchange at deploy time, `SVC-COMPILE-ENDPOINT`). It adds NO CQL semantics (`APP-DECOUPLE`).
  *
  * SPARK-FREE (`EXEC-SQL-BACKEND`, `TYPE-OFFLINE-COMPILE`, `SVC-STATELESS`): every op — translate,
  * compileSql, compilePipeline, describe — is pure IR → text / metadata. The service holds NO
  * `SparkSession`, needs NO Spark Connect URL, and touches NO data. Value-set membership lowers to
  * `valueset_<id>` views and FHIR retrieves to `retrieve_<Type>` views that the RUNTIME (the `cql()`
  * client / the deployed SDP) mounts and binds under the caller's identity (`SVC-BINDING-RUNTIME`) —
  * governance stays entirely at the executor (`SEC-GOVERNANCE`). This retires the reference-only
  * `DataProvider`/`TerminologyProvider` injection of the plan-proto model (`SVC-REF-PROVIDERS`); the
  * only signal the compiler needs is whether terminology is mounted (`terminology.isDefined`). */
object CompilerServiceApp {

  /** Bump on any wire-contract change (`SVC-PROTOCOL`). */
  val ProtocolVersion = "0.2.0-sql-backend"

  /** The compiler identity consumers key their caches on (`SVC-COMPILER-IDENTITY`, `PIPE-BARRIER-HASH`).
    *
    * Reported as `compilerVersion` on `/capabilities`, `/compile-pipeline` and `/describe`. CQL on Databricks stores
    * it beside each compiled library and, when it moves, full-refreshes that library's materializations
    * even where the emitted SQL is byte-identical — because an engine change can alter results WITHOUT
    * altering the SQL: the `cql_*` runtime UDFs are the engine's, and `cql_age_at(...)` reads exactly the
    * same before and after its semantics change. A hash of the emitted text cannot see that; this can.
    *
    * WHY NOT THE ENGINE'S OWN VERSION. `BuildInfo.version` reads the JAR manifest's
    * `Implementation-Version`, which the engine build does not stamp, so it is the constant
    * `"0.1.0-dev"` on every build and could never signal an upgrade. It is still reported, as
    * `engineVersion`, for provenance — it is just not something anyone can key on.
    *
    * RESOLUTION (the repo's standard specific-env -> default chain):
    *   - `CQL_COMPILER_VERSION` when set — the DEPLOYED path. `stage-compiler.sh` stamps the git sha, so
    *     the identity is stable for the life of a deployment and an App restart (scaling, idle wake,
    *     crash) does NOT invalidate anyone's materializations.
    *   - otherwise a per-JVM id minted here — the LOCAL path. A `val` in an object, so it is computed
    *     once per process: stable for the life of the service, different on every restart. Locally you
    *     restart this service precisely because you rebuilt the engine, so "changed on startup" is the
    *     signal you actually want, and it costs a full recompute of whatever you then open.
    */
  val CompilerVersion: String =
    sys.env.get("CQL_COMPILER_VERSION").map(_.trim).filter(_.nonEmpty)
      .getOrElse(s"dev-${System.currentTimeMillis()}")

  /** True when [[CompilerVersion]] was minted at startup rather than stamped by a deployment. */
  def isEphemeralVersion: Boolean = CompilerVersion.startsWith("dev-")

  def main(args: Array[String]): Unit = {
    val cfg = HostConfig.fromEnv()

    // A single, cheap, session-free CompileService (SVC-STATELESS): pure IR -> SQL text / metadata, no
    // Spark. `terminology` is only a SIGNAL (`terminology.isDefined` -> `hasTerminology`) that the runtime
    // mounts `valueset_<id>` views; `expand` is never called in the SQL backend, so the marker needs no
    // Spark session (see MountedTerminology). Per-request config (`data.fhir_catalog`,
    // `terminology.valueset_table`, version pins) travels IN the request `overrides` (BUILD-RESOLVE) and is
    // consumed engine-side by `Config.resolve`, so one shared service serves every request.
    val terminology: Option[TerminologyProvider] =
      if (cfg.terminologyMounted) Some(MountedTerminology) else None
    val service = new CompileService(terminology)

    val router = new CompilerRouter(service, cfg.token)
    val server = HttpServer.create(new InetSocketAddress(cfg.bindHost, cfg.port), 0)
    // A small thread pool so concurrent pipeline-update compiles (`SVC-STATELESS`, safe to parallelize)
    // don't serialize on the HttpServer's default single-threaded executor.
    server.setExecutor(Executors.newFixedThreadPool(cfg.threads))
    for (path <- Seq("/health", "/capabilities", "/translate", "/compile-sql", "/compile-pipeline",
                     "/describe", "/resource-schema", "/merge-cql")) {
      server.createContext(path, new HttpHandler {
        def handle(ex: HttpExchange): Unit = CompilerServiceApp.serve(router, ex)
      })
    }
    server.start()

    // Loud, parseable startup line so a launcher / the CQL on Databricks app dev server can read the bound endpoint.
    val bound = server.getAddress.getPort
    println(s"[CompilerService] ready endpoint=http://${cfg.bindHost}:$bound " +
      s"auth=${if (cfg.token.isDefined) "bearer" else "none"} backend=sql-spark-free " +
      s"terminology=${if (cfg.terminologyMounted) "mounted" else "off"} protocol=$ProtocolVersion " +
      s"compilerVersion=$CompilerVersion")
    // SVC-COMPILER-IDENTITY: say plainly which identity mode this process is in, because the two have
    // very different consequences for anyone caching against it.
    if (isEphemeralVersion)
      println(s"[CompilerService] compilerVersion is EPHEMERAL ($CompilerVersion) — minted at startup " +
        "because CQL_COMPILER_VERSION is unset. Every restart of this service invalidates cached " +
        "materializations and forces a full recompute (PIPE-BARRIER-HASH). Intended for LOCAL dev, " +
        "where a restart means you rebuilt the engine. A DEPLOYMENT must stamp CQL_COMPILER_VERSION " +
        "(stage-compiler.sh does) or every App restart will churn the warehouse.")
    System.out.flush()

    Runtime.getRuntime.addShutdownHook(new Thread(() => server.stop(0)))
    try Thread.currentThread().join()
    catch { case _: InterruptedException => () }
  }

  /** Bridge one HTTP exchange to the pure [[CompilerRouter]] and write its result. Kept tiny so the
    * routing/auth/serialization logic stays unit-testable without a socket (see the spec). */
  private def serve(router: CompilerRouter, ex: HttpExchange): Unit = {
    val res =
      try {
        val body = new String(ex.getRequestBody.readAllBytes(), StandardCharsets.UTF_8)
        val auth = Option(ex.getRequestHeaders.getFirst("Authorization"))
        router.handle(ex.getRequestMethod, ex.getRequestURI.getPath, auth, body)
      } catch {
        case t: Throwable => HttpResult.error(500, t)
      }
    val bytes = res.body.getBytes(StandardCharsets.UTF_8)
    ex.getResponseHeaders.add("Content-Type", "application/json")
    res.headers.foreach { case (name, value) => ex.getResponseHeaders.set(name, value) }
    ex.sendResponseHeaders(res.status, bytes.length.toLong)
    val os = ex.getResponseBody
    try os.write(bytes) finally os.close()
  }
}

/** The terminology-presence SIGNAL for the SQL backend (`EXEC-SQL-BACKEND`). Value sets lower to
  * `valueset_<id>` views the RUNTIME mounts, so the compiler only needs to know terminology IS mounted
  * (`terminology.isDefined` -> `hasTerminology`); the engine builds no expansion frame here, so `expand`
  * is never called. This marker carries the signal with NO Spark session, keeping the service Spark-free
  * (supersedes the reference-only `ConformedTerminologyProvider` of the plan-proto model). */
private object MountedTerminology extends TerminologyProvider {
  def expand(valueSetId: String): org.apache.spark.sql.DataFrame =
    throw new UnsupportedOperationException(
      "SQL backend: value sets lower to runtime-mounted valueset_<id> views; expand() is never called")
}

/** Host configuration, resolved from the environment (a Databricks App injects `PORT`; identity/auth
  * is provided by the deployment). All optional with dev-friendly defaults. */
final case class HostConfig(bindHost: String, port: Int, token: Option[String], threads: Int,
                            terminologyMounted: Boolean)

object HostConfig {
  def fromEnv(): HostConfig = {
    // A Databricks App container binds the port it is given; default 8080 for local dev. Bind 0.0.0.0
    // (NOT loopback like the engine's OssCompileHost) — the App is reached over the network; the
    // deployment fronts it with TLS + the platform's network policy.
    val port = sys.props.get("cqldatabricks.hostPort").orElse(sys.env.get("PORT"))
      .flatMap(s => scala.util.Try(s.trim.toInt).toOption).getOrElse(8080)
    val bindHost = sys.env.getOrElse("CQL_COMPILER_BIND", "0.0.0.0")
    // RFC 6750 bearer. When set, every non-/health request must carry `Authorization: Bearer <token>`.
    // TODO(SVC-COMPILE-ENDPOINT): the Databricks deployment swaps this static check for RFC 8693 OAuth
    // token-exchange validation (serverless->App); the wire header is unchanged (auth is the host's
    // concern, not the contract — see OssCompileHost).
    val token = sys.env.get("CQL_COMPILER_TOKEN").map(_.trim).filter(_.nonEmpty)
    val threads = sys.env.get("CQL_COMPILER_THREADS").flatMap(s => scala.util.Try(s.toInt).toOption)
      .getOrElse(Runtime.getRuntime.availableProcessors().max(2))
    // Whether the compiler signals that the runtime mounts value-set expansion views (`valueset_<id>`,
    // `hasTerminology`). Default ON: measures generally reference value sets and the runtime binds the
    // conformed store (`SVC-BINDING-RUNTIME`). Set CQL_TERMINOLOGY=off for a strictly data-free host.
    val terminologyMounted = !sys.env.get("CQL_TERMINOLOGY").map(_.trim.toLowerCase).contains("off")
    HostConfig(bindHost, port, token, threads, terminologyMounted)
  }
}

final case class HttpResult(status: Int, body: String, headers: Map[String, String] = Map.empty)

object HttpResult {
  def json(status: Int, value: ujson.Value): HttpResult = HttpResult(status, ujson.write(value))
  /** A loud error body (`CORE-NO-SILENT-MISLOWER`): never a silent empty success. */
  def error(status: Int, t: Throwable): HttpResult =
    json(status, ujson.Obj("error" -> s"${t.getClass.getSimpleName}: ${Option(t.getMessage).getOrElse("")}"))
  def error(status: Int, message: String): HttpResult = json(status, ujson.Obj("error" -> message))
}

/** The pure request router: (method, path, Authorization header, body) -> [[HttpResult]]. No sockets and
  * no Spark — every op is session-free (`EXEC-SQL-BACKEND`), so it is fully unit-testable. Holds one
  * shared [[CompileService]] (per-request config travels in the request `overrides`, `BUILD-RESOLVE`). */
final class CompilerRouter(service: CompileService, token: Option[String],
                           compilerVersion: String = CompilerServiceApp.CompilerVersion) {

  private val compileCache = new CompileResponseCache()

  /** SVC-COMPILE-CACHE — cache after authorization; timings include lookup, compile and serialization. */
  private def cachedCompile(path: String, body: String)(compile: => HttpResult): HttpResult = {
    val start = System.nanoTime()
    try {
      val (result, hit) = compileCache.getOrCompile(compilerVersion, path, body)(compile)
      result.copy(headers = result.headers ++ Map(
        "X-CQL-Compile-Cache" -> (if (hit) "HIT" else "MISS"),
        "Server-Timing" -> s"compile;dur=${(System.nanoTime() - start) / 1000000.0}"))
    } catch { case t: Throwable => HttpResult.error(400, t) }
  }

  def handle(method: String, path: String, authHeader: Option[String], body: String): HttpResult =
    path match {
      case "/health" => HttpResult.json(200, ujson.Obj("status" -> "ok"))
      case "/capabilities" if method == "GET" => capabilities
      case _ if !authorized(authHeader) =>
        HttpResult.error(401, "unauthorized: expected 'Authorization: Bearer <token>'")
      case "/translate" if method == "POST"        => translate(body)
      case "/compile-sql" if method == "POST"      => cachedCompile(path, body)(compileSql(body))
      case "/compile-pipeline" if method == "POST" => cachedCompile(path, body)(compilePipeline(body))
      case "/describe" if method == "POST"         => describe(body)
      case "/resource-schema" if method == "POST"  => resourceSchema(body)
      case "/merge-cql" if method == "POST"        => mergeCql(body)
      case "/translate" | "/compile-sql" | "/compile-pipeline" | "/describe" | "/resource-schema" |
           "/merge-cql" =>
        HttpResult.error(405, s"$path expects POST")
      case "/capabilities" => HttpResult.error(405, "/capabilities expects GET")
      case other => HttpResult.error(404, s"no such method: $other")
    }

  /** RFC 6750 bearer check. No configured token = open (local dev / SEC-LOCAL-IDENTITY). */
  private def authorized(authHeader: Option[String]): Boolean = token match {
    case None => true
    case Some(expected) =>
      authHeader.map(_.trim).exists { h =>
        h.regionMatches(true, 0, "Bearer ", 0, 7) && h.substring(7).trim == expected
      }
  }

  private def capabilities: HttpResult =
    HttpResult.json(200, ujson.Obj(
      "name" -> "compiler-app",
      "version" -> CompilerServiceApp.ProtocolVersion,
      "protocol" -> CompilerServiceApp.ProtocolVersion,
      // SVC-COMPILER-IDENTITY: the value consumers key their caches on. `version`/`protocol` above are
      // the WIRE contract and deliberately move only on a wire change, so they cannot serve this purpose.
      // CQL on Databricks reads this field first (`orchestrate_interactive_run`), which is why it must be here and
      // not only on the compile responses — the cheapest call answers the question.
      "compilerVersion" -> compilerVersion,
      "engineVersion" -> BuildInfo.version,
      // Method names advertise the served URL paths, which are lowercase kebab-case (BUILD-URL-KEBAB).
      "methods" -> ujson.Arr("translate", "compile-sql", "compile-pipeline", "describe", "resource-schema",
                             "merge-cql"),
      // The SQL backend is Spark-free end to end (EXEC-SQL-BACKEND): every op is pure IR -> text/metadata.
      // `resource-schema` is likewise Spark-free — the mount DDL is derived from the STATIC FHIR model
      // (ResourceStruct.structType), no SparkSession (SVC-RESOURCE-SCHEMA). One kebab path serves two
      // additive shapes: `{types:[...]}` -> mount DDL; empty/absent `types` -> `{resourceTypes:[...]}`,
      // the enumeration of every modeled FHIR resource (FhirModel.resourceTypes, sorted).
      "sparkFreeMethods" ->
        ujson.Arr("translate", "compile-sql", "compile-pipeline", "describe", "resource-schema", "merge-cql")))

  /** CQL -> ELM (authoring). Fail-loud: `ok:false` + diagnostics, never a silent pass
    * (`CORE-NO-SILENT-MISLOWER`). `includes` are extra library sources so cross-library `include`s
    * resolve (the registry supplies these, `REG-SOURCE-PROVIDER`). */
  private def translate(body: String): HttpResult = {
    val o = ujson.read(if (body.isEmpty) "{}" else body)
    val cql = o.obj.get("cql").flatMap(_.strOpt).getOrElse("")
    val includes = o.obj.get("includes").flatMap(_.arrOpt)
      .map(_.flatMap(_.strOpt).toSeq).getOrElse(Seq.empty)
    val result = CqlToElmCli.translateText(cql, includes)
    HttpResult.json(200, ujson.Obj(
      "elm" -> result.elmJson.map(ujson.Str(_): ujson.Value).getOrElse(ujson.Null),
      "diagnostics" -> ujson.Arr.from(result.errors.map(ujson.Str(_))),
      "ok" -> result.elmJson.isDefined))
  }

  /** SVC-NOTEBOOK-SESSION-SCOPE — header-less fragments -> one library, the latest declaration winning
    * ([[MergeCql]]). A header or a syntax error is a loud 400. */
  private def mergeCql(body: String): HttpResult =
    try HttpResult.json(200, MergeCql.handle(body))
    catch { case t: Throwable => HttpResult.error(400, t) }

  /** ELM/CQL -> a Spark SQL SCRIPT (runtime `cql()` compile, `EXEC-SQL-BACKEND`, `PIPE-RUNTIME-COMPILE`):
    * `{statements:[{name,kind,sql}], target, defines}`. The client runs each statement via `spark.sql` in
    * order and `SELECT`s `target`. Same wire contract as the engine's `OssCompileHost` so a client points
    * at either host. Session-free; a compile failure is a loud 400 (`CORE-NO-SILENT-MISLOWER`). */
  private def compileSql(body: String): HttpResult =
    try {
      val res = service.compileSql(CompileRequest.fromJson(body))
      HttpResult.json(200, ujson.Obj(
        "statements" -> ujson.Arr.from(res.statements.map(CompilerRouter.statementJson)),
        "target" -> res.target,
        "defines" -> ujson.Arr.from(res.defines),
        // EMIT-SQL-VALUE-COLUMN, EMIT-SQL-OBJECT-PREFIX — the per-define read contract. The client finds each
        // define's object here; without it a prefixed compile has no name to read (CLIENT-INLINE-WAREHOUSE).
        "shapes" -> ujson.Arr.from(res.shapes.map(CompilerRouter.shapeJson))))
    } catch { case t: Throwable => HttpResult.error(400, t) }

  /** The compile-once PIPELINE PLAN (`PIPE-SQL-DAG-REUSE`): one dataset recipe per materialized define,
    * `{name, target, reads, query, statements}`. The deployed SDP turns each recipe into one
    * `@dp.materialized_view` (no per-node `cql()`, `SVC-SDP-PACKAGE`). Session-free; failure is a loud 400. */
  private def compilePipeline(body: String): HttpResult =
    try {
      val plan = service.compilePipeline(CompileRequest.fromJson(body))
      HttpResult.json(200, ujson.Obj(
        "batchProtocol" -> "cql-batch-v1",
        // SVC-COMPILER-IDENTITY: the SAME identity `/capabilities` reports, so a consumer that reads it
        // from the compile response and one that reads it from discovery cannot disagree. NOT
        // `plan.compilerVersion` — that is the engine's unstamped build constant (see CompilerVersion).
        "compilerVersion" -> compilerVersion,
        "engineVersion" -> BuildInfo.version,
        "datasets" -> ujson.Arr.from(plan.datasets.map { d =>
          val o = ujson.Obj(
            "name" -> d.name,
            "target" -> d.target,
            "reads" -> ujson.Arr.from(d.reads),
            "query" -> d.query,
            "statements" -> ujson.Arr.from(d.statements.map(CompilerRouter.statementJson)))
          d.durable.foreach { identity =>
            o("durable") = identity.toJson
            o("library") = ujson.Str(d.library)
            o("libraryVersion") = ujson.Str(d.libraryVersion)
            o("define") = ujson.Str(d.define)
            o("kind") = ujson.Str(d.kind)
            o("valueColumn") = ujson.Str(d.valueColumn)
          }
          o
        })))
    } catch { case t: Throwable => HttpResult.error(400, t) }

  /** The neutral pipeline graph — the parameterized metadata-driven SDP reads this to build its dataset
    * DAG dynamically (`SVC-SDP-PACKAGE`). Session-free; failure is a loud 400. */
  private def describe(body: String): HttpResult =
    try {
      HttpResult.json(200, CompilerRouter.graphJson(service.describe(CompileRequest.fromJson(body)), compilerVersion))
    } catch { case t: Throwable => HttpResult.error(400, t) }

  /** The canonical `__resource` mount DDL per FHIR type (`SVC-RESOURCE-SCHEMA`) — the schema a data
    * seeder needs to stand up a contract-correct `retrieve_<Type>` on the `(__subject_id, __resource)`
    * mount contract (`DATA-CONTRACT` / `DATA-RETRIEVE-VIEW`) WITHOUT re-deriving the engine's FHIR model.
    * Pure model derivation from the STATIC schema (`ResourceStruct.structType`), so it stays Spark-free
    * (`EXEC-SQL-BACKEND`) — no `SparkSession`, only the catalyst `StructType` on the runtime classpath.
    * The engine is a CALLED public API here, never edited (`APP-DECOUPLE`).
    *
    * Two shapes on the one kebab-case path (additive wire contract, `SVC-RESOURCE-SCHEMA`):
    *   - `{types:[...]}`  -> `{schemas:{Type -> {modeled, patientContext?, ddl?, schema?}}}` (per-type mount
    *     DDL, and the same struct as Spark's JSON schema, `CLIENT-RESOURCE-SCHEMA`).
    *     An unmodeled type is reported (`modeled:false`), never a silent omission
    *     (`CORE-NO-SILENT-MISLOWER`); `patientContext` tells the seeder whether the type is keyed by a
    *     patient subject (Patient itself, or a `subject`-carrying resource) so it can compute `__subject_id`.
    *   - EMPTY/ABSENT `types` -> `{resourceTypes:[...sorted...]}` — the ENUMERATION of every modeled FHIR
    *     resource (`FhirModel.resourceTypes`), so a consumer (the CQL on Databricks app Data Sources editor) offers only
    *     bindable entities instead of a hardcoded list. Deterministic (sorted ascending); FHIR R4 only. */
  private def resourceSchema(body: String): HttpResult =
    try {
      val o = ujson.read(if (body.isEmpty) "{}" else body)
      val types = o.obj.get("types").flatMap(_.arrOpt)
        .map(_.flatMap(_.strOpt).map(_.trim).filter(_.nonEmpty).toSeq).getOrElse(Seq.empty)
      if (types.isEmpty)
        HttpResult.json(200, ujson.Obj(
          "resourceTypes" -> ujson.Arr.from(FhirModel.resourceTypes.toSeq.sorted)))
      else {
        val schemas = types.distinct.map { t =>
          val entry =
            if (!FhirSchema.isModeled(t)) ujson.Obj("modeled" -> false)
            else ujson.Obj(
              "modeled" -> true,
              "patientContext" -> FhirModel.isPatientContext(t),
              "ddl" -> ResourceStruct.structType(t).toDDL,
              // CLIENT-RESOURCE-SCHEMA: the same struct as Spark's JSON schema, which a client turns into a
              // `StructType` with no Spark session (DDL parsing needs one).
              "schema" -> ujson.read(ResourceStruct.structType(t).json))
          t -> (entry: ujson.Value)
        }
        HttpResult.json(200, ujson.Obj("schemas" -> ujson.Obj.from(schemas)))
      }
    } catch { case t: Throwable => HttpResult.error(400, t) }
}

object CompilerRouter {
  /** One compiled statement on the wire: `{name, kind, sql}`. */
  def statementJson(s: SqlStatementOut): ujson.Obj =
    ujson.Obj("name" -> s.name, "kind" -> s.kind, "sql" -> s.sql)

  /** One [[DefineShape]] as the engine's OssCompileHost `/compile-sql` writes it. */
  def shapeJson(s: DefineShape): ujson.Obj =
    ujson.Obj("define" -> s.define, "object" -> s.obj, "kind" -> s.kind, "valueColumn" -> s.valueColumn)

  /** Serialize a [[LibraryGraph]] to the same JSON shape as the engine's OssCompileHost `/describe`. */
  def graphJson(g: LibraryGraph, compilerVersion: String = CompilerServiceApp.CompilerVersion): ujson.Obj =
    ujson.Obj(
      "defines" -> ujson.Arr.from(g.defines),
      "dependencies" -> ujson.Obj.from(g.dependencies.view.mapValues(ujson.Arr.from(_)).toSeq),
      "boundaries" -> ujson.Arr.from(g.boundaries.toSeq.sorted),
      "materializedDefines" -> ujson.Arr.from(g.materializedDefines.toSeq.sorted),
      // SVC-COMPILER-IDENTITY: one identity across every endpoint that reports one. `g.compilerVersion`
      // is the engine's unstamped build constant, preserved as `engineVersion` for provenance.
      "compilerVersion" -> compilerVersion,
      "engineVersion" -> g.compilerVersion,
      "terminologyVersion" -> g.terminologyVersion.map(ujson.Str(_)).getOrElse(ujson.Null),
      // EMIT-DESCRIBE-RETRIEVE-TYPES: every retrieved type, so a caller binds exactly the data a run needs.
      "retrieveTypes" -> ujson.Arr.from(g.retrieveTypes),
      // BIND-PROVIDER-REQUEST: each retrieve with its id and request.
      "retrieves"     -> ujson.Arr.from(g.retrieves.map(r => ujson.Obj("id" -> r.id, "request" -> r.requestJson))),
      "valueSets"     -> ujson.Arr.from(g.valueSets.map { case (id, v) => ujson.Obj("id" -> id, "version" -> v.map(ujson.Str(_)).getOrElse(ujson.Null)) }))
}
