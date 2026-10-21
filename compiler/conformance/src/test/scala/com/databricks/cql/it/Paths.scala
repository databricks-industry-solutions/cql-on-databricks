package com.databricks.cql.it

import java.io.File
import java.nio.charset.StandardCharsets
import java.nio.file.Files

/** Locations wired by the Gradle `test` task system properties (see conformance
  * build.gradle), with sensible fallbacks for running from the repo root. */
object Paths {
  private def prop(key: String, default: String): File =
    new File(sys.props.getOrElse(key, default))

  /** Repo root, used to locate sibling modules (e.g. client) independent of the corpus
    * location. Set by the Gradle `test` task; falls back to the CWD when run from the repo root. */
  def rootDir: File   = prop("cqldatabricks.rootDir", ".")
  def elmDir: File    = prop("cqldatabricks.elmDir", "compiler/conformance/build/elm")
  def cqlDir: File    = prop("cqldatabricks.cqlDir", "compiler/conformance/src/test/resources/cql")
  /** The single golden folder (CONF-GOLDEN-PLAN). Per fixture, it mirrors the dump folder:
    * `<goldenDir>/<fixture>/<artifact>` (e.g. `TrivialBoolean/ir.txt`,
    * `FhirNumerator/optimized.ir.txt`, `<measure>/pushdown.unresolved-plan.txt`). Each artifact is
    * tree-compared by `harness.Golden`; present → guard, absent → skip. Overridable via
    * `-Dcqldatabricks.goldenDir` (e.g. to stage/verify a freshly-blessed golden outside `src/`). */
  def goldenDir: File = prop("cqldatabricks.goldenDir", "compiler/conformance/src/test/resources/golden")
  def dataDir: File   = prop("cqldatabricks.dataDir", "compiler/conformance/src/test/resources/data/fhir")

  private def walkFiles(root: File, ext: String): Seq[File] =
    if (!root.isDirectory) Seq.empty
    else {
      val out = scala.collection.mutable.ArrayBuffer.empty[File]
      def walk(d: File): Unit = Option(d.listFiles()).getOrElse(Array.empty).sortBy(_.getName).foreach { f =>
        if (f.isDirectory) walk(f) else if (f.getName.endsWith(ext)) out += f
      }
      walk(root)
      out.toSeq
    }

  /** All generated ELM files (the translator writes them flat; recursive walk is robust). */
  def listElm(): Seq[File] = walkFiles(elmDir, ".json")

  /** Locate a generated ELM file by library name.
    *
    * A name with NO authored `.cql` behind it is a HARD ERROR, not a missing file to discover later
    * (`CORE-NO-SILENT-MISLOWER`). When a library is renamed, a stale `build/elm/<old-name>.json` from before the rename
    * still satisfies a hard-coded old name, so a spec keeps compiling the SUPERSEDED draft and passes.
    * That is exactly how `OfflineCompileSpec` once went on lowering a measure under its old name after
    * the corpus renamed it. Resolving against the authored CQL makes the rename fail loudly. */
  def elm(name: String): File = {
    val f = listElm().find(_.getName == s"$name.json")
    if (f.isEmpty && !cqlExists(name))
      throw new NoSuchElementException(
        s"no ELM and no authored CQL for library '$name' — renamed or removed upstream? " +
          s"Known libraries: ${listElm().map(_.getName.stripSuffix(".json")).sorted.mkString(", ")}")
    f.getOrElse(new File(elmDir, s"$name.json"))
  }

  /** Locate an authored `.cql` source by library name (corpus is laid out in subdirs). */
  def cql(name: String): File =
    walkFiles(cqlDir, ".cql").find(_.getName == s"$name.cql")
      .getOrElse(throw new NoSuchElementException(
        s"no authored CQL for library '$name' under $cqlDir — renamed or removed?"))

  private def cqlExists(name: String): Boolean =
    walkFiles(cqlDir, ".cql").exists(_.getName == s"$name.cql")

  def read(f: File): String = new String(Files.readAllBytes(f.toPath), StandardCharsets.UTF_8)
  def write(f: File, s: String): Unit = {
    Files.createDirectories(f.getParentFile.toPath)
    Files.write(f.toPath, s.getBytes(StandardCharsets.UTF_8))
  }
}
