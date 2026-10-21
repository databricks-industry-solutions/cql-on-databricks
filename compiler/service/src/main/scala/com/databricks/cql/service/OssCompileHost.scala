package com.databricks.cql.service

import com.sun.net.httpserver.{HttpExchange, HttpHandler, HttpServer}
import com.databricks.cql.passes.BuildInfo

import java.net.InetSocketAddress
import java.nio.charset.StandardCharsets

/** The local compile host, for development and tests (`BUILD-COMPILE-SERVICE`): a
  * ZERO-DEPENDENCY JDK [[com.sun.net.httpserver.HttpServer]] fronting a [[CompileService]].
  *
  * Endpoint paths are lowercase kebab-case (`BUILD-URL-KEBAB`); the in-code method names keep the
  * Scala idiom (`compileSql`/`compilePipeline`).
  *
  *   POST /compile-sql {elm, deps?, target?, boundaries?, context?, overrides?, grainFirst?}
  *                -> {"statements":[{"name","kind","sql"}], "target":"…", "defines":[...]}  (EMIT-SQL-EXECUTE)
  *   POST /compile-pipeline {elm, deps?, boundaries?, context?, overrides?, grainFirst?}
  *                -> {"compilerVersion":"…", "datasets":[{"name","target","reads":[…],"query","statements":[{"name","kind","sql"}]}]}  (PIPE-SQL-DAG-REUSE)
  *   POST /describe  {elm, deps?, context?, overrides?, grainFirst?}
  *                -> {"defines":[...], "dependencies":{...}, "boundaries":[...],
  *                    "materializedDefines":[...], "compilerVersion":"…", "terminologyVersion":…}
  *   GET  /capabilities -> {"compilerVersion":"…"}
  *   GET  /health -> {"status":"ok"}
  *
  * OSS DECOUPLING: this is a plain HTTP endpoint with NO platform tie and NO auth (localhost default).
  * The client reaches it with the standard `Authorization: Bearer` header ONLY if a fronting host
  * requires one — auth is the host's concern, not the wire contract. The Databricks App is a SEPARATE
  * host of the SAME [[CompileService]] (in the accelerator repo) that adds OAuth (RFC 8693 token
  * exchange + RFC 6750 bearer); nothing here depends on it. Binding to 127.0.0.1 keeps the default host
  * local-only; a deployment fronts it with its own TLS/auth. */
final class OssCompileHost(service: CompileService, port: Int = 0) {
  private val server: HttpServer = HttpServer.create(new InetSocketAddress("127.0.0.1", port), 0)
  server.createContext("/compile-sql", new HttpHandler {
    def handle(ex: HttpExchange): Unit = OssCompileHost.this.handleCompileSql(ex)
  })
  server.createContext("/compile-pipeline", new HttpHandler {
    def handle(ex: HttpExchange): Unit = OssCompileHost.this.handleCompilePipeline(ex)
  })
  server.createContext("/describe", new HttpHandler {
    def handle(ex: HttpExchange): Unit = OssCompileHost.this.handleDescribe(ex)
  })
  server.createContext("/capabilities", new HttpHandler {
    def handle(ex: HttpExchange): Unit = OssCompileHost.this.handleCapabilities(ex)
  })
  server.createContext("/health", new HttpHandler {
    def handle(ex: HttpExchange): Unit = respond(ex, 200, """{"status":"ok"}""")
  })

  /** Start serving (idempotent per instance). Returns `this` for fluent use. */
  def start(): OssCompileHost = { server.start(); this }

  /** Stop serving immediately. */
  def stop(): Unit = server.stop(0)

  /** The actual bound port (useful when constructed with `port = 0` = ephemeral). */
  def boundPort: Int = server.getAddress.getPort

  /** The base URL a client points its remote provider at. */
  def endpoint: String = s"http://127.0.0.1:$boundPort"

  /** POST /compile-sql → the compiled SQL SCRIPT (EMIT-SQL-EXECUTE): `{statements:[{name,kind,sql}],
    * target, defines, shapes:[{define,object,kind,valueColumn}]}`. The client runs each statement via
    * `spark.sql` in order and `SELECT`s `target`; `shapes` is the per-define read contract
    * (`EMIT-SQL-VALUE-COLUMN`) so a consumer never re-derives a column name or probes a schema.
    * A compile failure (unsupported node, bad ELM) is a loud 400 (CORE-NO-SILENT-MISLOWER). */
  private def handleCompileSql(ex: HttpExchange): Unit =
    try {
      if (ex.getRequestMethod != "POST") respond(ex, 405, """{"error":"POST only"}""")
      else {
        val body = new String(ex.getRequestBody.readAllBytes(), StandardCharsets.UTF_8)
        val res = service.compileSql(CompileRequest.fromJson(body))
        val out = ujson.Obj(
          "statements" -> ujson.Arr.from(res.statements.map(s =>
            ujson.Obj("name" -> s.name, "kind" -> s.kind, "sql" -> s.sql))),
          "target"  -> res.target,
          "defines" -> ujson.Arr.from(res.defines),
          "shapes"  -> ujson.Arr.from(res.shapes.map(s =>
            ujson.Obj("define" -> s.define, "object" -> s.obj,
                      "kind" -> s.kind, "valueColumn" -> s.valueColumn))))
        respond(ex, 200, ujson.write(out))
      }
    } catch {
      case t: Throwable =>
        respond(ex, 400, ujson.write(ujson.Obj("error" -> s"${t.getClass.getSimpleName}: ${Option(t.getMessage).getOrElse("")}")))
    }

  /** POST /compile-pipeline → the compile-once [[PipelinePlan]] as JSON (`PIPE-SQL-DAG-REUSE`): one dataset
    * recipe per materialized define — `{name, target, reads, statements}`. The reference pipeline turns
    * each recipe into one `@dp.materialized_view` (no per-node `cql()`). A failure is a loud 400. */
  private def handleCompilePipeline(ex: HttpExchange): Unit =
    try {
      if (ex.getRequestMethod != "POST") respond(ex, 405, """{"error":"POST only"}""")
      else {
        val body = new String(ex.getRequestBody.readAllBytes(), StandardCharsets.UTF_8)
        val plan = service.compilePipeline(CompileRequest.fromJson(body))
        val out = ujson.Obj(
          "compilerVersion" -> plan.compilerVersion,
          "batchProtocol" -> "cql-batch-v1",
          "datasets" -> ujson.Arr.from(plan.datasets.map { d =>
          val recipe = ujson.Obj(
            "name"           -> d.name,
            "target"         -> d.target,
            "reads"          -> ujson.Arr.from(d.reads),
            "query"          -> d.query,
            "library"        -> d.library,
            "libraryVersion" -> d.libraryVersion,
            "define"         -> d.define,
            "sqlFile"        -> d.sqlFile,
            "kind"           -> d.kind,
            "valueColumn"    -> d.valueColumn,
            // DATA-RETRIEVE-BARRIER-OPT: present only on a materialized READ recipe (absent → `null`), so
            // an existing consumer that ignores the key is unaffected.
            "retrieveRead"   -> d.retrieveRead.map(readJson).getOrElse(ujson.Null),
            "statements" -> ujson.Arr.from(d.statements.map(s =>
              ujson.Obj("name" -> s.name, "kind" -> s.kind, "sql" -> s.sql))))
          d.durable.foreach(identity => recipe("durable") = identity.toJson)
          recipe
        }))
        respond(ex, 200, ujson.write(out))
      }
    } catch {
      case t: Throwable =>
        respond(ex, 400, ujson.write(ujson.Obj("error" -> s"${t.getClass.getSimpleName}: ${Option(t.getMessage).getOrElse("")}")))
    }

  /** POST /describe → the neutral [[com.databricks.cql.meta.LibraryGraph]] as JSON (the graph an
    * external pipeline reads to build its own dataset DAG). A failure is a loud 400, like /compile. */
  /** DATA-RETRIEVE-BARRIER-OPT: a materialized read's coordinates on the wire — the provenance vehicle for
    * a dataset with no define to name it. Optionals render as `null` rather than being omitted, so the key
    * set is stable across recipes and a consumer can read a field without a presence test. */
  private def readJson(s: ReadCoordinates): ujson.Obj = ujson.Obj(
    "resourceType"        -> s.resourceType,
    "profile"             -> s.profile.map(ujson.Str(_)).getOrElse(ujson.Null),
    "codeProperty"        -> s.codeProperty.map(ujson.Str(_)).getOrElse(ujson.Null),
    "codeFilterKind"      -> s.codeFilterKind,
    "valueSetId"          -> s.valueSetId.map(ujson.Str(_)).getOrElse(ujson.Null),
    "codes"               -> ujson.Arr.from(s.codes.map { case (sys, c) =>
                               ujson.Obj("system" -> sys, "code" -> c) }),
    "dateProperty"        -> s.dateProperty.map(ujson.Str(_)).getOrElse(ujson.Null),
    "dateFiltered"        -> s.dateFiltered,
    "periodProperty"      -> s.periodProperty.map(ujson.Str(_)).getOrElse(ujson.Null),
    "periodLowInclusive"  -> s.periodLowInclusive.map(ujson.Str(_)).getOrElse(ujson.Null),
    "periodHighExclusive" -> s.periodHighExclusive.map(ujson.Str(_)).getOrElse(ujson.Null),
    "consumedBy"          -> ujson.Arr.from(s.consumedBy),
    "callSites"           -> s.callSites)

  private def handleDescribe(ex: HttpExchange): Unit =
    try {
      if (ex.getRequestMethod != "POST") respond(ex, 405, """{"error":"POST only"}""")
      else {
        val body = new String(ex.getRequestBody.readAllBytes(), StandardCharsets.UTF_8)
        val g = service.describe(CompileRequest.fromJson(body))
        val out = ujson.Obj(
          "defines"             -> ujson.Arr.from(g.defines),
          "dependencies"        -> ujson.Obj.from(g.dependencies.view.mapValues(ujson.Arr.from(_)).toSeq),
          "boundaries"          -> ujson.Arr.from(g.boundaries.toSeq.sorted),
          "materializedDefines" -> ujson.Arr.from(g.materializedDefines.toSeq.sorted),
          "compilerVersion"     -> g.compilerVersion,
          "terminologyVersion"  -> g.terminologyVersion.map(ujson.Str(_)).getOrElse(ujson.Null),
          // EMIT-DESCRIBE-ENCODING: the carrier encoding + the per-resource demand contract (sorted for
          // determinism) a downstream pipeline validates its typed-subset coverage against.
          "encoding"            -> g.encoding,
          "retrieveDemand"      -> ujson.Obj.from(g.retrieveDemand.toSeq.sortBy(_._1).map { case (k, v) => k -> ujson.Arr.from(v) }),
          // EMIT-SQL-VALUE-COLUMN: the per-define ROW shape + the one payload column, so `describe` alone
          // tells a consumer how to READ each define (no second compile call, no schema probe).
          "defineKinds"         -> ujson.Obj.from(g.defineKinds.toSeq.sortBy(_._1).map { case (k, v) => k -> ujson.Str(v) }),
          "valueColumn"         -> g.valueColumn,
          // EMIT-DESCRIBE-RETRIEVE-TYPES: every retrieved type, so a caller binds exactly the data a run needs.
          "retrieveTypes"       -> ujson.Arr.from(g.retrieveTypes))
        respond(ex, 200, ujson.write(out))
      }
    } catch {
      case t: Throwable =>
        respond(ex, 400, ujson.write(ujson.Obj("error" -> s"${t.getClass.getSimpleName}: ${Option(t.getMessage).getOrElse("")}")))
    }

  /** GET /capabilities → static compiler capabilities. */
  private def handleCapabilities(ex: HttpExchange): Unit =
    try {
      val out = ujson.Obj("compilerVersion" -> BuildInfo.version)
      respond(ex, 200, ujson.write(out))
    } catch {
      case t: Throwable =>
        respond(ex, 500, ujson.write(ujson.Obj("error" -> s"${t.getClass.getSimpleName}: ${Option(t.getMessage).getOrElse("")}")))
    }

  private def respond(ex: HttpExchange, code: Int, body: String): Unit = {
    val bytes = body.getBytes(StandardCharsets.UTF_8)
    ex.getResponseHeaders.add("Content-Type", "application/json")
    ex.sendResponseHeaders(code, bytes.length.toLong)
    val os = ex.getResponseBody
    try os.write(bytes) finally os.close()
  }
}
