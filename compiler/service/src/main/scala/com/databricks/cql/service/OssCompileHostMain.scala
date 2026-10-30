package com.databricks.cql.service

/** A runnable entry point for the local compile host, for development and tests (`BUILD-COMPILE-SERVICE`):
  * front a [[CompileService]] with the zero-dependency JDK
  * [[OssCompileHost]] and serve `/compile-sql` + `/describe` + `/health` over HTTP until interrupted.
  *
  * SPARK-FREE (EXEC-SQL-BACKEND, a strengthening of ARCH-PURITY): the service compiles CQL+ELM to a SQL
  * SCRIPT and describes the neutral graph — pure IR → text/metadata, no `SparkSession`, no Connect client,
  * no server round-trip (`TYPE-OFFLINE-COMPILE`). The Python `client` points its `RemoteProvider`
  * at the endpoint printed on startup, receives the compiled SQL, and runs it in ITS OWN Spark session.
  * The accelerator's Databricks App is a SEPARATE, OAuth-fronted host of the SAME [[CompileService]].
  *
  * DATA-FREE by construction: no [[com.databricks.cql.retrieve.TerminologyProvider]] is wired, so
  * this standalone host compiles libraries whose value-set mounts the runtime provides externally. A host
  * that must report a pinned terminology version injects the adapter into the [[CompileService]] itself.
  *
  * `-Dcqldatabricks.hostPort` sets the HTTP port (default `0` = an ephemeral port printed on startup). Run via
  * `./gradlew :compiler:service:runCompileHost`. */
object OssCompileHostMain {

  def main(args: Array[String]): Unit = {
    val port = sys.props.get("cqldatabricks.hostPort").flatMap(s => scala.util.Try(s.toInt).toOption).getOrElse(0)

    val host = new OssCompileHost(new CompileService(), port).start()

    // Loud, parseable startup line so a launcher (the E2E gate) can read the bound endpoint from stdout.
    // stdout is line-buffered under Gradle; flush so the reader sees it immediately.
    println(s"[OssCompileHost] ready endpoint=${host.endpoint}")
    System.out.flush()

    // Shut down cleanly on Ctrl-C / SIGTERM (frees the port).
    Runtime.getRuntime.addShutdownHook(new Thread(() => host.stop()))

    // Block the main thread forever; the HttpServer serves on its own threads until the JVM is killed.
    try Thread.currentThread().join()
    catch { case _: InterruptedException => () }
  }
}
