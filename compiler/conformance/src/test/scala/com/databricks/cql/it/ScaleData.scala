package com.databricks.cql.it

import java.io.File
import java.nio.charset.StandardCharsets
import java.nio.file.{Files, StandardCopyOption}

/** Deterministically REPLICATES a hand-authored FHIR NDJSON corpus N times with fresh identities, for the
  * throughput scale harnesses ([[ReferenceCrossoverScaleSpec]], [[FixtureScaleSpec]]) — NOT a correctness gate (which
  * runs on the base deck). Replication is the whole game of a scale test: to time an engine at K× the
  * patients you need K× the DISTINCT subjects, each a self-consistent resource graph, so per-subject work
  * actually multiplies.
  *
  * RE-KEYING. The corpus identities are opaque tokens — descriptive ids in the measure data
  * (`enc-inside`, `data/measures/<library>`), short hand ids (`p1`, `med1`, `c3`) in the fixture deck (`data/fhir`) — and appear two ways: as a top-level
  * `"id":"<x>"` and inside a `"reference":"Type/<x>"`. In JSON both are delimited by `"` or `/` on each
  * side, so we collect the actual id set (parsed from every resource's `id`) and rewrite each id ONLY at
  * that boundary (`(?<=["/])<id>(?=")`) — precise enough that a short id never matches a code/system value
  * or a longer id that contains it. Replica k appends `-r<k>` to every id AND to the references that point
  * at it in ONE pass, so k is a disjoint copy of the whole graph whose internal references still resolve.
  * Both sides of the differential read the SAME replicated data (the Spark adapter from the temp dir, the
  * Java reference from the returned map), exactly as the base corpus feeds both (CONF-TESTDATA). */
object ScaleData {

  final case class Scaled(dir: File, resourcesByType: Map[String, Seq[String]], subjectIds: Seq[String]) {
    val patients: Int = subjectIds.size
  }

  /** Replicate `resourceTypes` from `baseDir` `factor`× into a fresh temp dir (value-set files copied
    * verbatim — they carry no subject identity), returning the dir the Spark adapter reads plus the
    * per-type replicated lines and the replicated Patient ids the Java reference evaluates per subject.
    *
    * @param baseDir       the base data dir (e.g. `data/measures/<library>` or `data/fhir`)
    * @param resourceTypes the NDJSON resource files to replicate (the measure's / fixture's declared types)
    * @param factor        the replication multiplier (`1` = one relabeled copy; exercises the same path) */
  def replicate(baseDir: File, resourceTypes: Seq[String], factor: Int): Scaled = {
    require(factor >= 1, s"scale factor must be >= 1, got $factor")
    val out = Files.createTempDirectory(s"scale-x$factor-").toFile
    out.deleteOnExit()

    // Value-set expansions and any other file: copy verbatim (no subject identity to re-key).
    Option(baseDir.listFiles()).getOrElse(Array.empty)
      .filter(f => f.isFile && !resourceTypes.contains(f.getName.stripSuffix(".ndjson")))
      .foreach(f => Files.copy(f.toPath, new File(out, f.getName).toPath, StandardCopyOption.REPLACE_EXISTING))

    def readLines(f: File): Seq[String] =
      if (f.exists) scala.io.Source.fromFile(f, "UTF-8").getLines().filter(_.trim.nonEmpty).toList else Nil

    val baseByType = resourceTypes.map(t => t -> readLines(new File(baseDir, s"$t.ndjson"))).toMap

    // The id set across ALL replicated types, longest-first so the alternation never prefers a shorter id
    // that is a prefix of another (the `"`/`/` boundaries already prevent that, but this is belt-and-braces).
    val ids = baseByType.values.flatten
      .flatMap(l => scala.util.Try(ujson.read(l)("id").str).toOption).toSeq.distinct.sortBy(-_.length)
    val idRe = if (ids.isEmpty) None
      else Some(("(?<=[\"/])(" + ids.map(java.util.regex.Pattern.quote).mkString("|") + ")(?=\")").r)
    def relabel(line: String, k: Int): String = idRe.fold(line)(_.replaceAllIn(line, m => s"${m.matched}-r$k"))

    val resourcesByType = resourceTypes.map { t =>
      val replicated = (0 until factor).flatMap(k => baseByType(t).map(relabel(_, k)))
      Files.write(new File(out, s"$t.ndjson").toPath,
        replicated.mkString("\n").getBytes(StandardCharsets.UTF_8))
      t -> replicated
    }.toMap

    // Subject ids are the replicated Patient top-level ids (the Java reference evaluates once per id).
    val subjectIds = resourcesByType.getOrElse("Patient", Nil).map(l => ujson.read(l)("id").str)
    Scaled(out, resourcesByType, subjectIds)
  }
}
