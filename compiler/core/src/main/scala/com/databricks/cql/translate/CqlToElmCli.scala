package com.databricks.cql.translate

import org.cqframework.cql.cql2elm.{CqlCompilerException, CqlCompilerOptions, CqlTranslator, LibraryManager, ModelManager,
  StringLibrarySourceProvider}

import java.nio.charset.StandardCharsets
import java.nio.file.{Files, Path, Paths}
import scala.jdk.CollectionConverters._

/** The engine's CQL -> ELM front end (TECH-ELM-INPUT, `04` section 2). Lives in the **engine** module
  * because CQL is the engine's public API surface — translation ships, it is not test-only.
  *
  * OUR OWN tool built on the cqframework `cql-to-elm` LIBRARY (`CqlTranslator` API) — not a
  * wrapper around the cqframework CLI. One approach, not a mix; **in-process, no subprocess,
  * no service**. Version-aligned (via `cql-bom`) with the Java ground-truth engine so both
  * sides share one ELM schema (`CONF-GROUND-TRUTH`, `TECH-ELM-INPUT`).
  *
  * Two entry points: `translateText`/`translateFile` (the reusable library API, called by
  * `Pipeline.normalizeCql`) and `main` (the build-time corpus translator the `translateCorpus`
  * Gradle task invokes).
  *
  * GAP: the specs named a translation *service* (an HTTP server). With the
  * cqframework ANTLR (Kotlin) no longer colliding with Spark's `org.antlr`, translation runs
  * in-process in the engine — no server. The CLI `main` remains the build-time corpus step.
  *
  * ERROR POLICY. ELM is validated downstream (the round-trip / ground-truth tests, `CONF-*`),
  * not by this tool: a file with translation errors is **not written** (diagnostics are
  * printed), so the missing/invalid ELM fails the test that needs it. The build tool does
  * not adjudicate measure correctness and does not exit non-zero on CQL errors.
  *
  * usage: CqlToElmCli <inputCqlDirOrFile> <outputElmDir>
  */
object CqlToElmCli {

  def main(args: Array[String]): Unit = {
    if (args.length != 2) {
      System.err.println("usage: CqlToElmCli <inputCqlDirOrFile> <outputElmDir>")
      sys.exit(2)
    }
    val input = Paths.get(args(0)).toAbsolutePath
    val outDir = Paths.get(args(1)).toAbsolutePath
    Files.createDirectories(outDir)

    val cqlFiles = collectCqlFiles(input)
    if (cqlFiles.isEmpty) {
      System.err.println(s"no .cql files found under $input")
      sys.exit(1)
    }

    // Register a multi-library measure's `include` sources scoped to the file's OWN directory
    // (TECH-ELM-INPUT, CONF-GATE-MEASURE). Each measure dir (`<corpus>/measures/<m>/`) is a SELF-CONTAINED
    // library set that ships its own copies of the shared libraries (FHIRHelpers and the helper
    // libraries it includes). Registering the WHOLE corpus as one source set collides those per-measure
    // duplicates — the cqframework LibraryManager raises "Multiple libraries for id … resolved" and
    // emits NO ELM for any measure. Grouping sources by directory isolates each measure's dependency
    // closure (every `include` resolves to a sibling); flat fixtures (`operators/`, `fhir/`) include at
    // most FHIRHelpers, which is the bundled source, so their sibling set is a harmless no-op.
    val sourcesByDir: Map[Path, Seq[String]] =
      cqlFiles.groupBy(_.getParent).map { case (dir, fs) =>
        dir -> fs.map(f => new String(Files.readAllBytes(f), StandardCharsets.UTF_8))
      }

    var written = 0
    cqlFiles.foreach { cql =>
      val result = translateText(
        new String(Files.readAllBytes(cql), StandardCharsets.UTF_8),
        sourcesByDir.getOrElse(cql.getParent, Seq.empty))
      result.errors.foreach(e => System.err.println(s"  [${cql.getFileName}] $e"))
      result.elmJson match {
        case Some(elm) =>
          val out = outDir.resolve(baseName(cql) + ".json")
          Files.write(out, elm.getBytes(StandardCharsets.UTF_8))
          written += 1
          println(s"translated ${cql.getFileName} -> ${out.getFileName}")
        case None =>
          System.err.println(s"SKIPPED ${cql.getFileName}: translation errors (no ELM emitted)")
      }
    }
    println(s"translated $written of ${cqlFiles.size} CQL file(s) -> $outDir")
  }

  /** The outcome of one translation: the ELM JSON (absent if error-severity diagnostics
    * were raised) plus any error messages for visibility. */
  final case class Result(elmJson: Option[String], errors: Seq[String])

  /** SVC-COMPILE-BATCH — keep the dependency ELM already produced by the front end. */
  final case class ClosureResult(root: Result, dependencies: Seq[String])

  def translateFile(cqlFile: Path): Result =
    translateText(new String(Files.readAllBytes(cqlFile), StandardCharsets.UTF_8))

  /** Translate CQL text to ELM JSON via the cqframework 4.x library. `ModelManager()`
    * default-loads the System model; non-System models (`using FHIR`/`QICore`/`USCore`) resolve
    * through the cqframework `ModelInfoProvider` ServiceLoader (the `org.cqframework:quick` jar
    * ships the FHIR/QICore/USCore modelinfo), so no manual model wiring is needed.
    *
    * `extraSources` are additional library sources registered so cross-library `include`s resolve
    * (a measure library and its dependencies, CONF-GATE-MEASURE); empty for a self-contained
    * single-library fixture. */
  def translateText(cqlText: String, extraSources: Seq[String] = Seq.empty): Result =
    translate(cqlText, extraSources, retainClosure = false).root

  /** SVC-COMPILE-BATCH — one request-local LibraryManager, no retranslation to obtain include ELM.
    * Uses cqframework's library graph, not a reconstruction or patch of its generated output. */
  def translateClosure(cqlText: String, extraSources: Seq[String]): ClosureResult =
    translate(cqlText, extraSources, retainClosure = true)

  private def translate(cqlText: String, extraSources: Seq[String], retainClosure: Boolean): ClosureResult = {
    val modelManager = new ModelManager()
    // Annotations ON (a cqframework default, kept): the emitted ELM carries the CQL-source
    // narrative (`annotation`/`a:s` tags) so a dumped `elm.json` traces back to its CQL text.
    // Result types ON: the translator stamps each node's inferred `resultTypeName`/
    // `resultTypeSpecifier`, so the emitted ELM records the front end's own typing.
    // Both are non-semantic to the kernel — Load drops them either way (see ElmCodec).
    val options = CqlCompilerOptions.defaultOptions()
    options.getOptions.add(CqlCompilerOptions.Options.EnableAnnotations)
    options.getOptions.add(CqlCompilerOptions.Options.EnableResultTypes)
    val libraryManager = new LibraryManager(modelManager, options)
    // The bundled FHIRHelpers, so a library that writes `include FHIRHelpers version '4.0.1'`
    // resolves it; it is never added to a library that does not (LANG-NO-IMPLICIT-INCLUDE).
    // Plus any cross-library sources so a multi-library measure's includes resolve. An include resolves
    // to the source declaring exactly its name and version; a version no source declares fails the
    // translation (HL7 CQL Author's Guide, Libraries: an include that states a version references that version).
    libraryManager.getLibrarySourceLoader.registerProvider(
      new StringLibrarySourceProvider((FhirHelpersSource.cql +: extraSources).asJava))
    // The cqframework parser can THROW (not just collect diagnostics) on some malformed input —
    // e.g. a reserved word as the library name NPEs its error listener. Treat any such failure as
    // an error Result so one bad fixture never crashes the corpus build or the engine (the missing
    // ELM then fails the test that needs it — CORE-NO-SILENT-MISLOWER, never a fabricated plan).
    try {
      // The author's text exactly as written (LANG-NO-IMPLICIT-INCLUDE).
      val translator = CqlTranslator.fromText(cqlText, libraryManager)
      val errors = translator.getErrors.asScala.toSeq
        .filter(_.getSeverity == CqlCompilerException.ErrorSeverity.Error)
        .map(e => s"${Option(e.getLocator).map(_.toString).getOrElse("?")}: ${e.getMessage}")
      val elm = if (errors.isEmpty) Some(translator.toJson) else None
      val dependencies = if (errors.nonEmpty || !retainClosure) Seq.empty else
        translator.getLibraries.asScala.toSeq
          .filterNot { case (_, lib) => lib.getIdentifier == translator.toELM.getIdentifier }
          .sortBy { case (id, _) => (id.getId, Option(id.getVersion).getOrElse("")) }
          .map { case (_, lib) => CqlTranslator.convertToJson(lib) }
      ClosureResult(Result(elm, errors), dependencies)
    } catch {
      case e: Throwable => ClosureResult(
        Result(None, Seq(s"translation failed: ${e.getClass.getSimpleName}: ${e.getMessage}")), Seq.empty)
    }
  }

  def collectCqlFiles(input: Path): Seq[Path] = {
    if (Files.isDirectory(input)) {
      Files.walk(input).iterator().asScala
        .filter(p => Files.isRegularFile(p) && p.toString.toLowerCase.endsWith(".cql"))
        .toSeq.sortBy(_.toString)
    } else if (Files.isRegularFile(input) && input.toString.toLowerCase.endsWith(".cql")) {
      Seq(input)
    } else Seq.empty
  }

  private def baseName(p: Path): String = {
    val n = p.getFileName.toString
    if (n.toLowerCase.endsWith(".cql")) n.substring(0, n.length - 4) else n
  }
}
