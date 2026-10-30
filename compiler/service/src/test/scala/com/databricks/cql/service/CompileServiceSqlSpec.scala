package com.databricks.cql.service

import com.databricks.cql.env.SourceBinding
import com.databricks.cql.error.UnsupportedNode
import com.databricks.cql.spark.SystemColumns
import com.databricks.cql.translate.CqlToElmCli
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** Phase 4 test for the SQL compile path (EMIT-SQL-EXECUTE): `CompileService.compileSql` lowers a real
  * CQL library (translated in-process to ELM) to a SQL SCRIPT — server-free, no Spark session. It locks
  * the service wiring (front end -> SqlFrameLowering -> rendered statements + target + defines); the live
  * proto-vs-SQL diff is the Phase-5 gate. */
@RunWith(classOf[JUnitRunner])
class CompileServiceSqlSpec extends AnyFunSuite {

  private val cql =
    """library Test version '1.0.0'
      |using FHIR version '4.0.1'
      |context Patient
      |define "InitialPopulation": true
      |define "AllEncounters": [Encounter]
      |define "HasEncounter": exists "AllEncounters"
      |""".stripMargin

  private def elm: String = CqlToElmCli.translateText(cql).elmJson
    .getOrElse(fail("CQL translation produced no ELM"))

  // A context-free, model-free library — no `using`, no `context` (LANG-CONTEXT-DEFAULT tier 4).
  private def contextFreeElm: String = CqlToElmCli.translateText(
    "library Tiny version '1.0.0'\ndefine \"One\": 1 + 1\n").elmJson.getOrElse(fail("no ELM"))

  test("compileSql lowers a Patient library to a SQL script of named views, in dependency order") {
    val res = CompileService.compileSql(
      CompileRequest(elmJson = elm, target = Some("HasEncounter"), context = Some("Patient")),
      hasTerminology = false)

    val names = res.statements.map(_.name)
    // A named context base, a grain-first relation view for the retrieve, and a scalar define view.
    assert(names.contains("__frame_base"))
    assert(names.contains("AllEncounters"))
    assert(names.contains("HasEncounter"))
    // Dependency order: the relation view precedes the scalar that reduces it.
    assert(names.indexOf("AllEncounters") < names.indexOf("HasEncounter"))
    // The whole thing is CREATE OR REPLACE TEMP VIEW statements (no barrier at the default threshold).
    assert(res.statements.forall(_.kind == "view"))
    // The base reads the mounted context retrieve; the script is auditable SQL text.
    assert(res.script.contains("FROM retrieve_Patient"))
    assert(res.script.contains("CREATE OR REPLACE TEMP VIEW AllEncounters AS"))
  }

  test("the target and defines metadata are carried through") {
    val res = CompileService.compileSql(
      CompileRequest(elmJson = elm, target = Some("HasEncounter"), context = Some("Patient")),
      hasTerminology = false)
    assert(res.target == "HasEncounter")
    assert(res.defines.contains("AllEncounters") && res.defines.contains("HasEncounter"))
  }

  /** EMIT-SQL-VALUE-COLUMN: the per-define READ CONTRACT. A consumer must be able to read any result without
    * re-deriving `SqlNames.sanitize` for a column name or probing the produced schema to learn the row shape
    * (`ARCH-NO-RUNTIME-SCHEMA`) — so both travel as metadata, and the value column is the SAME for both kinds. */
  test("compileSql publishes a DefineShape per define: uniform value column + row kind") {
    val res = CompileService.compileSql(
      CompileRequest(elmJson = elm, context = Some("Patient")), hasTerminology = false)

    assert(res.shapes.map(_.define) == res.defines, "one shape per define, in the same order")
    // EVERY define publishes the same payload column — that is the whole point of the contract.
    assert(res.shapes.forall(_.valueColumn == "__val"))

    val rel = res.shapes.find(_.define == "AllEncounters").getOrElse(fail("no AllEncounters shape"))
    val sca = res.shapes.find(_.define == "HasEncounter").getOrElse(fail("no HasEncounter shape"))
    assert(rel.kind == DefineShape.Relation) // grain-first: (__subject_id, __ordinal, __val)
    assert(sca.kind == DefineShape.Scalar)   // one row per subject: (__subject_id, __val)
    // The define name identifies the OBJECT, and is not the column the value lives in.
    assert(sca.obj == "HasEncounter")
    val stmt = res.statements.find(_.name == sca.obj).getOrElse(fail("no HasEncounter statement"))
    assert(stmt.sql.contains("AS __val") && !stmt.sql.contains("AS HasEncounter"))
  }

  test("exists over the relation define renders a presence join, absent -> false") {
    val res = CompileService.compileSql(
      CompileRequest(elmJson = elm, target = Some("HasEncounter"), context = Some("Patient")),
      hasTerminology = false)
    val hasEnc = res.statements.find(_.name == "HasEncounter").getOrElse(fail("no HasEncounter statement"))
    assert(hasEnc.sql.contains("LEFT OUTER JOIN"))
    assert(hasEnc.sql.contains("FROM AllEncounters"))
    assert(hasEnc.sql.contains("coalesce("))
  }

  // --- LANG-CONTEXT-DEFAULT: spec-conformant context resolution -----------------------------------

  test("a context-free define with NO request context lowers to a data-free frame, not retrieve_Patient") {
    // No `using`, no `context`, no request context (LANG-CONTEXT-DEFAULT → Unfiltered).
    val res = CompileService.compileSql(
      CompileRequest(elmJson = contextFreeElm, target = Some("One"), context = None),
      hasTerminology = false)
    assert(res.target == "One")
    assert(!res.script.contains("retrieve_"),
      s"context-free define must not force a retrieve, got:\n${res.script}")
    // The Unfiltered one-subject frame (ARCH-SET-EVAL), seeded with no data.
    assert(res.script.contains("__subject_id"))
  }

  test("a library that DECLARES context Patient stays Patient-scoped when the request omits context") {
    // The `cql` above declares `context Patient`; the request carries NONE (tier 2: declared context wins).
    val res = CompileService.compileSql(
      CompileRequest(elmJson = elm, target = Some("InitialPopulation"), context = None),
      hasTerminology = false)
    assert(res.script.contains("retrieve_Patient"),
      s"declared context Patient must scope to the patient frame, got:\n${res.script}")
  }

  test("an explicit request context overrides the library's declared context (tier 1 wins)") {
    // Same Patient-declared library, but the caller explicitly asks for Unfiltered → data-free frame.
    val res = CompileService.compileSql(
      CompileRequest(elmJson = elm, target = Some("InitialPopulation"), context = Some("Unfiltered")),
      hasTerminology = false)
    assert(!res.script.contains("retrieve_Patient"),
      s"explicit Unfiltered must win over declared Patient, got:\n${res.script}")
  }

  // --- BIND-MANIFEST / BIND-RETRIEVE-UDF: the client-supplied retrieve UDTF binding table -----------

  test("a sources binding emits FROM [catalog.]fn(<explicit args>), no bare mount (BIND-RETRIEVE-UDF)") {
    val res = CompileService.compileSql(
      CompileRequest(elmJson = elm, target = Some("HasEncounter"), context = Some("Patient"),
        sources = Map(
          "Encounter" -> SourceBinding("fetch_encounter", Some("acme.fhir")), // bound, catalog-qualified
          "Patient"   -> SourceBinding("fetch_patient", None))),              // bound, default schema
      hasTerminology = false)
    // the bound Encounter retrieve resolves to its catalog-qualified UDTF
    assert(res.script.contains("acme.fhir.fetch_encounter("),
      s"expected the bound Encounter UDTF call, got:\n${res.script}")
    // the Patient context base routes through its bound UDTF too, with no predicates and no parameters
    assert(res.script.contains("fetch_patient(NULL, NULL, array(), NULL, NULL, NULL, map())"),
      s"expected the bound Patient context-base call, got:\n${res.script}")
    // EMIT-ROOT-INDEPENDENT: no retrieve argument names a library (BIND-EXPLICIT-PARAMS)
    assert(!res.script.contains("named_struct('id'"), s"a library argument leaked:\n${res.script}")
    assert(!res.script.contains("fetch_patient(parse_json('{}')"),
      s"the retired empty-envelope context base leaked:\n${res.script}")
    // no bare `retrieve_<Type>` view mount survives under the UDTF flag
    assert(!res.script.contains("FROM retrieve_"), s"a bare mount leaked:\n${res.script}")
  }

  test("CompileRequest.fromJson parses the sources binding table leniently (BIND-MANIFEST)") {
    val json =
      s"""{"elm": ${ujson.Str(elm)},
         | "sources": {
         |   "Encounter": {"function": "fetch_encounter", "catalog": "acme.fhir"},
         |   "Condition": {"function": "fetch_condition"},
         |   "Broken":    {"catalog": "x"}
         | }}""".stripMargin
    val req = CompileRequest.fromJson(json)
    assert(req.sources("Encounter") == SourceBinding("fetch_encounter", Some("acme.fhir")))
    assert(req.sources("Condition") == SourceBinding("fetch_condition", None)) // optional catalog defaulted
    assert(!req.sources.contains("Broken"))                                    // no `function` → ignored
  }

  // --- ELM origin: library, libraryVersion, define on DatasetRecipe -------------------

  test("pipeline recipe carries ELM origin: library, libraryVersion, define (single-library)") {
    val req = CompileRequest(elmJson = elm, context = Some("Patient"),
      overrides = Map("materialize.mode" -> "all"))
    val plan = CompileService.compilePipeline(req, hasTerminology = false)
    assert(plan.datasets.nonEmpty, "expected at least one recipe")
    plan.datasets.foreach { r =>
      assert(r.library == "Test", s"recipe ${r.name}: expected library 'Test', got '${r.library}'")
      assert(r.libraryVersion == "1.0.0", s"recipe ${r.name}: expected version '1.0.0', got '${r.libraryVersion}'")
      // The define must be the RAW CQL name (spaces, casing preserved) — not sanitized.
      assert(!r.define.contains("_") || r.define == r.define,
        s"recipe ${r.name}: define should be the raw CQL name, got '${r.define}'")
    }
    // Spot-check a specific recipe whose CQL name has no special characters.
    val hasEnc = plan.datasets.find(_.define == "HasEncounter")
    assert(hasEnc.isDefined, s"expected a HasEncounter recipe, got ${plan.datasets.map(_.name)}")
    assert(hasEnc.get.name == "test_1_0_0__hasencounter",
      s"expected dataset name 'test_1_0_0__hasencounter', got '${hasEnc.get.name}'")
    assert(hasEnc.get.define == "HasEncounter")
  }

  // --- PIPE-EMIT-SQL: deploy-ready SDP SQL on each DatasetRecipe ------------------------------------

  test("pipeline recipe carries deploy-ready sqlFile (PIPE-EMIT-SQL)") {
    val req = CompileRequest(elmJson = elm, context = Some("Patient"),
      overrides = Map("materialize.mode" -> "all"))
    val plan = CompileService.compilePipeline(req, hasTerminology = false)
    assert(plan.datasets.nonEmpty, "expected at least one recipe")
    plan.datasets.foreach { r =>
      assert(r.sqlFile.nonEmpty, s"recipe ${r.name}: sqlFile must not be empty")
      // Every recipe is materialized (cutPlan filters to materializedDefines).
      assert(r.sqlFile.contains("CREATE OR REFRESH MATERIALIZED VIEW"),
        s"recipe ${r.name}: sqlFile must contain CREATE OR REFRESH MATERIALIZED VIEW, got:\n${r.sqlFile.take(200)}")
      // The DDL names the published dataset.
      assert(r.sqlFile.contains(r.name),
        s"recipe ${r.name}: sqlFile must reference the dataset name '${r.name}'")
      // The body is the same declarative query.
      assert(r.sqlFile.contains(r.query),
        s"recipe ${r.name}: sqlFile must embed the query")
      // ELM origin comment (library is non-empty in this test fixture).
      assert(r.sqlFile.startsWith("-- Library:"),
        s"recipe ${r.name}: sqlFile must start with an ELM origin comment, got:\n${r.sqlFile.take(100)}")
    }
  }

  test("parameter overrides produce a param_hash segment in dataset names (PIPE-EMIT-SQL)") {
    val paramCql =
      """library ParamLib version '2.0.0'
        |parameter "Measurement Period" Interval<DateTime>
        |define "PeriodStart": start of "Measurement Period"
        |""".stripMargin
    val paramElm = CqlToElmCli.translateText(paramCql).elmJson.getOrElse(fail("no ELM"))
    val params = Map("Measurement Period" -> """Interval[@2026-01-01, @2027-01-01)""")
    val req = CompileRequest(elmJson = paramElm, context = Some("Unfiltered"),
      overrides = Map("materialize.mode" -> "all"), parameters = params)
    val plan = CompileService.compilePipeline(req, hasTerminology = false)
    assert(plan.datasets.nonEmpty, "expected at least one recipe")
    val name = plan.datasets.head.name
    // The name must follow {lib}_{ver}_{hash}__{define} — 4 underscore-separated segments before __.
    assert(name.startsWith("paramlib_2_0_0_"), s"expected paramlib_2_0_0_ prefix, got $name")
    assert(name.contains("__periodstart"), s"expected __periodstart define suffix, got $name")
    // The hash segment is 8 hex chars between the version and the __ separator.
    val prefix = name.substring(0, name.indexOf("__"))
    val segments = prefix.split("_")
    // paramlib, 2, 0, 0, <8-char-hash> → at least 5 segments.
    assert(segments.length >= 5, s"expected param_hash segment in prefix '$prefix'")
    val hash = segments.last
    assert(hash.length == 8 && hash.forall("0123456789abcdef".contains(_)),
      s"expected 8-char hex hash, got '$hash' in name '$name'")
  }

  // --- EMIT-UDF-QUALIFY: schema-qualified cql_* UDF calls -------------------------------------------

  test("udfSchema qualifies cql_* UDF calls in compileSql output (EMIT-UDF-QUALIFY)") {
    // CalculateAgeInYearsAt triggers a `cql_age_at` UDF call.
    val ageCql =
      """library UdfTest version '1.0.0'
        |using FHIR version '4.0.1'
        |include FHIRHelpers version '4.0.1' called FHIRHelpers
        |context Patient
        |define "Age": CalculateAgeInYearsAt(Patient.birthDate, @2026-01-01)
        |""".stripMargin
    val ageElm = CqlToElmCli.translateText(ageCql, Seq(FhirHelpersCql.text)).elmJson.getOrElse(fail("no ELM"))
    // Without udfSchema — bare function names.
    val bare = CompileService.compileSql(
      CompileRequest(elmJson = ageElm, target = Some("Age")), hasTerminology = false)
    assert(bare.script.contains("cql_age_at("), s"expected bare cql_age_at call:\n${bare.script}")
    assert(!bare.script.contains("runtime.cql_age_at"),
      s"bare mode must not qualify:\n${bare.script}")
    // With udfSchema — qualified function names.
    val qualified = CompileService.compileSql(
      CompileRequest(elmJson = ageElm, target = Some("Age"),
        udfSchema = Some("acme.runtime")),
      hasTerminology = false)
    assert(qualified.script.contains("acme.runtime.cql_age_at("),
      s"expected qualified cql_age_at call:\n${qualified.script}")
    // Non-cql_* functions stay unqualified.
    assert(!qualified.script.contains("acme.runtime.coalesce"),
      s"non-cql_* functions must not be qualified:\n${qualified.script}")
  }

  test("udfSchema qualifies cql_* UDF calls in compilePipeline output (EMIT-UDF-QUALIFY)") {
    val ageCql =
      """library PipeUdfTest version '1.0.0'
        |using FHIR version '4.0.1'
        |include FHIRHelpers version '4.0.1' called FHIRHelpers
        |context Patient
        |define "Age": CalculateAgeInYearsAt(Patient.birthDate, @2026-01-01)
        |""".stripMargin
    val ageElm = CqlToElmCli.translateText(ageCql, Seq(FhirHelpersCql.text)).elmJson.getOrElse(fail("no ELM"))
    val plan = CompileService.compilePipeline(
      CompileRequest(elmJson = ageElm, udfSchema = Some("rt"),
        overrides = Map("materialize.mode" -> "all")),
      hasTerminology = false)
    assert(plan.datasets.nonEmpty)
    // Both the statements SQL and the declarative query must be qualified.
    val recipe = plan.datasets.find(_.define == "Age").getOrElse(fail("no Age recipe"))
    assert(recipe.query.contains("rt.cql_age_at("),
      s"expected qualified cql_age_at in query:\n${recipe.query}")
    assert(recipe.sqlFile.contains("rt.cql_age_at("),
      s"expected qualified cql_age_at in sqlFile:\n${recipe.sqlFile}")
  }

  test("CompileRequest.fromJson parses sources.udfs.schema (EMIT-UDF-QUALIFY)") {
    val json = s"""{"elm": ${ujson.Str(elm)}, "sources": {"udfs": {"schema": "acme.rt"}}}"""
    val req = CompileRequest.fromJson(json)
    assert(req.udfSchema.contains("acme.rt"))
    // Absent udfs entry → None.
    val json2 = s"""{"elm": ${ujson.Str(elm)}}"""
    assert(CompileRequest.fromJson(json2).udfSchema.isEmpty)
  }

  // --- BUILD-COMPILE-LINK / ARCH-SCHEDULE-ACYCLIC: multi-library measures ---------------------------
  // A two-library measure reproducing a supplemental-data-element self-export
  // (`define "Supplemental Sex": SDE."Supplemental Sex"` — the measure re-exports a SAME-NAMED define from a dependency).
  // This shape once StackOverflowed the un-linked service path: the qualified cross-library ref collided
  // with the local define name and the Schedule pass mistook it for a self-cycle.

  private val sdeHelperCql =
    """library SdeHelper version '1.0.0'
      |using FHIR version '4.0.1'
      |context Patient
      |define "Supplemental Sex": Patient.gender
      |""".stripMargin

  private val multiLibCql =
    """library Meas version '1.0.0'
      |using FHIR version '4.0.1'
      |include SdeHelper version '1.0.0' called SDE
      |context Patient
      |define "Initial Population": exists [Encounter]
      |define "Supplemental Sex": SDE."Supplemental Sex"
      |""".stripMargin

  private def translate(cql: String, deps: Seq[String] = Seq.empty): String =
    CqlToElmCli.translateText(cql, deps).elmJson
      .getOrElse(fail("CQL translation produced no ELM: " + CqlToElmCli.translateText(cql, deps).errors.mkString("; ")))

  test("a multi-library measure links its supplied deps and compiles (BUILD-COMPILE-LINK)") {
    // The measure's include closure is shipped as `deps`; the service flattens root + deps before lowering.
    val res = CompileService.compileSql(
      CompileRequest(
        elmJson = translate(multiLibCql, Seq(sdeHelperCql)),
        deps    = Seq(translate(sdeHelperCql)),
        target  = Some("Supplemental Sex")),
      hasTerminology = false)
    assert(res.statements.nonEmpty)
    assert(res.defines.contains("Supplemental Sex"))
  }

  test("the same measure fed WITHOUT its deps fails loudly, never StackOverflows (ARCH-SCHEDULE-ACYCLIC)") {
    // The un-linked root still carries the qualified same-named re-export. Before the fix this overflowed the
    // scheduler; it must now degrade to a loud compile error (CORE-NO-SILENT-MISLOWER) instead.
    val thrown = intercept[Throwable] {
      CompileService.compileSql(
        CompileRequest(elmJson = translate(multiLibCql, Seq(sdeHelperCql)), target = Some("Supplemental Sex")),
        hasTerminology = false)
    }
    assert(!thrown.isInstanceOf[StackOverflowError],
      "a same-named cross-library re-export must not overflow the Schedule pass (ARCH-SCHEDULE-ACYCLIC)")
    assert(thrown.isInstanceOf[UnsupportedNode],
      s"expected a loud UnsupportedNode for the unresolved cross-library ref, got ${thrown.getClass.getName}")
  }

  // --- DATA-RETRIEVE-BARRIER-OPT: materialized reads published as dataset recipes -------------------

  /** A measure whose SAME value-set read is read by two defines — the shape the reuse gate rewards. */
  private val sharedReadCql =
    """library ReadTest version '1.0.0'
      |using FHIR version '4.0.1'
      |valueset "Diabetes": 'urn:oid:2.999.1.101'
      |context Patient
      |define "DiabetesA": [Condition: "Diabetes"]
      |define "DiabetesB": [Condition: "Diabetes"]
      |define "Both": exists "DiabetesA" and exists "DiabetesB"
      |""".stripMargin

  private def readPlan(retrieves: String): PipelinePlan =
    CompileService.compilePipeline(
      CompileRequest(
        elmJson   = CqlToElmCli.translateText(sharedReadCql).elmJson.getOrElse(fail("no ELM")),
        context   = Some("Patient"),
        overrides = Map("materialize.mode" -> "all", "materialize.retrieves" -> retrieves)),
      hasTerminology = true)

  test("retrieves=none publishes no read datasets (the default is inert on the pipeline path too)") {
    assert(readPlan("none").datasets.forall(_.retrieveRead.isEmpty))
  }

  test("a materialized read becomes its own DatasetRecipe carrying its coordinates") {
    val reads = readPlan("all").datasets.filter(_.retrieveRead.isDefined)
    assert(reads.sizeIs == 1, s"expected one read recipe, got ${reads.map(_.name)}")
    val s = reads.head
    val c = s.retrieveRead.get

    assert(c.resourceType == "Condition")
    assert(c.codeFilterKind == "valueset")
    assert(c.valueSetId.contains("urn:oid:2.999.1.101"))
    assert(c.consumedBy.toSet == Set("DiabetesA", "DiabetesB"))
    assert(c.callSites == 2)

    // The published dataset shape is the mount's rows post-filter: no `__ordinal`, payload `__resource`.
    assert(s.kind == DefineShape.Resource)
    assert(s.valueColumn == SystemColumns.Resource)
    // A read has no define to name it — its provenance is the coordinates above.
    assert(s.define.isEmpty)
    // It reads only the EXTERNAL mount + terminology table, so it is a SOURCE node of the dataset graph
    // — which is exactly what lets N measures share one task for it.
    assert(s.reads.isEmpty, s"a read recipe should declare no sibling reads, got ${s.reads}")
    assert(s.sqlFile.contains("CREATE OR REFRESH MATERIALIZED VIEW"))
  }

  test("consuming defines declare the read dataset as a read — the SDP dependency edge") {
    val plan  = readPlan("all")
    val read  = plan.datasets.find(_.retrieveRead.isDefined).get
    val users = plan.datasets.filter(d => Set("DiabetesA", "DiabetesB").contains(d.define))
    assert(users.sizeIs == 2)
    users.foreach { u =>
      assert(u.reads.contains(read.name), s"${u.define} should read ${read.name}, got ${u.reads}")
      assert(u.query.contains(read.name), s"${u.define}'s query should read the published read by name")
    }
    // The read is declared BEFORE its consumers, so the dataset list stays in dependency order.
    assert(plan.datasets.indexWhere(_.retrieveRead.isDefined) < plan.datasets.indexWhere(_.define == "DiabetesA"))
  }

  test("read dataset names are library-qualified and disjoint from the retrieve_<Type> mount namespace") {
    val s = readPlan("all").datasets.find(_.retrieveRead.isDefined).get
    assert(s.name.matches("readtest_1_0_0__read_condition_diabetes_[0-9a-f]{8}"),
      s"unexpected read dataset name '${s.name}'")
    assert(!s.name.contains("retrieve_"))
  }

  // -- EMIT-SQL-OBJECT-PREFIX ------------------------------------------------------------------

  test("objectPrefix is OFF by default — emitted object names stay bare") {
    val res = CompileService.compileSql(
      CompileRequest(elmJson = elm, target = Some("HasEncounter"), context = Some("Patient")),
      hasTerminology = false)
    assert(res.statements.exists(_.name == "HasEncounter"),
      s"expected a bare 'HasEncounter' object, got ${res.statements.map(_.name)}")
    assert(!res.statements.exists(_.name.contains("__HasEncounter")))
  }

  test("objectPrefix namespaces every define-derived object AND the shape a client reads back by") {
    val res = CompileService.compileSql(
      CompileRequest(elmJson = elm, target = Some("HasEncounter"), context = Some("Patient"),
        objectPrefix = Some("Test_1_0_0")),
      hasTerminology = false)

    assert(res.statements.exists(_.name == "Test_1_0_0__HasEncounter"),
      s"expected a prefixed object, got ${res.statements.map(_.name)}")
    assert(res.target == "Test_1_0_0__HasEncounter", s"target should be prefixed, got '${res.target}'")
    // The SHAPE is what the client uses to find a define's object (it must never re-derive the name),
    // so a prefix that reached the script but not the shape would strand every consumer.
    val shape = res.shapes.find(_.define == "HasEncounter").getOrElse(fail("no shape for HasEncounter"))
    assert(shape.obj == "Test_1_0_0__HasEncounter", s"shape.obj not prefixed: '${shape.obj}'")
    // Cross-references INSIDE the script resolve to the prefixed names too — otherwise the script would
    // select from objects it never created.
    val sql = res.statements.map(_.sql).mkString("\n")
    assert(!sql.contains("FROM AllEncounters"), "an unprefixed cross-reference survived in the script")
  }

  test("a prefixed compile of two same-named libraries produces disjoint object namespaces") {
    // The collision this requirement exists for: both libraries define `Numerator`, and in ONE session
    // `CREATE OR REPLACE TEMP VIEW Numerator` from the second silently rebinds the first.
    def lib(name: String) = CqlToElmCli.translateText(
      s"library $name version '1.0.0'\ndefine \"Numerator\": true\n").elmJson.getOrElse(fail("no ELM"))
    def objects(name: String) = CompileService.compileSql(
      CompileRequest(elmJson = lib(name), target = Some("Numerator"), objectPrefix = Some(name)),
      hasTerminology = false).statements.map(_.name).toSet

    val a = objects("MeasureA")
    val b = objects("MeasureB")
    assert(a.contains("MeasureA__Numerator") && b.contains("MeasureB__Numerator"))
    assert(a.intersect(b).isEmpty, s"namespaces overlap: ${a.intersect(b)}")
  }

  // -- RUN-RESULTS-BY-EVAL / DEC-EVAL-ID-LOOKUP -------------------------------------------------

  test("RUN-RESULTS-BY-EVAL: with an evalId, a recipe reads a published sibling's rows for that evaluation only") {
    val req = CompileRequest(elmJson = elm, context = Some("Patient"), evalId = Some("abcd1234"),
      overrides = Map("materialize.mode" -> "all"))
    val plan = CompileService.compilePipeline(req, hasTerminology = false)
    val reader = plan.datasets.find(_.reads.nonEmpty).getOrElse(fail("no recipe reads a sibling"))
    val flat = reader.query.replaceAll("\\s+", " ")
    reader.reads.foreach { sibling =>
      assert(flat.contains(s"( SELECT * EXCEPT (`__eval_id`) FROM $sibling WHERE (__eval_id = 'abcd1234') ) AS __scoped_"),
        reader.query)
      // The evaluator relocates every occurrence of a dataset name to its three-part results address
      // (PIPE-CROSS-SCHEMA-QUALIFY); a name used as an alias would become `AS c.s.t`, which does not parse.
      assert(!flat.contains(s"AS $sibling"), reader.query)
    }
    // No bare read of a sibling survives beside the scoped one.
    val bare = CompileService.compilePipeline(req.copy(evalId = None), hasTerminology = false)
    assert(!bare.datasets.exists(_.query.contains("__eval_id")))
    assert(CompileRequest.fromJson("""{"elm": "{}", "evalId": "abcd1234"}""").evalId.contains("abcd1234"))
    assert(CompileRequest.fromJson("""{"elm": "{}"}""").evalId.isEmpty)
  }

  // -- PIPE-READS-SORTED ------------------------------------------------------------------------

  test("PIPE-READS-SORTED: a recipe's reads are sorted, not in the order its SQL first references them") {
    // "Both" references "Zed" before "Alpha", so a first-seen walk lists Zed first.
    val src =
      """library Order version '1.0.0'
        |using FHIR version '4.0.1'
        |context Patient
        |define "Zed": exists [Encounter]
        |define "Alpha": exists [Condition]
        |define "Both": "Zed" and "Alpha"
        |""".stripMargin
    val orderElm = CqlToElmCli.translateText(src).elmJson.getOrElse(fail("no ELM"))
    val plan = CompileService.compilePipeline(
      CompileRequest(elmJson = orderElm, context = Some("Patient"), overrides = Map("materialize.mode" -> "all")),
      hasTerminology = false)
    val both = plan.datasets.find(_.define == "Both").getOrElse(fail(s"no Both recipe in ${plan.datasets.map(_.name)}"))
    assert(both.reads.sizeIs == 2, s"Both should read Zed and Alpha, got ${both.reads}")
    assert(both.query.indexOf(both.reads.last) < both.query.indexOf(both.reads.head),
      s"the test needs Zed referenced first in the SQL: ${both.query}")
    assert(both.reads == both.reads.sorted, s"reads not sorted: ${both.reads}")
    assert(plan.datasets.forall(d => d.reads == d.reads.sorted))
  }

  // -- BUILD-COMPILE-LINK: a caller-resolved CQL closure ---------------------------------------

  test("deps supplied as {cql} objects are registered as translation sources so includes resolve") {
    val dep =
      """library DepLib version '1.0.0'
        |define "FromDep": 41 + 1
        |""".stripMargin
    val root =
      """library RootLib version '1.0.0'
        |include DepLib version '1.0.0' called Dep
        |define "UsesDep": Dep."FromDep"
        |""".stripMargin

    // Without the closure the root cannot translate at all — the include has no source.
    val bare = intercept[IllegalArgumentException] {
      CompileService.compileSql(CompileRequest(cql = Some(root), target = Some("UsesDep")),
        hasTerminology = false)
    }
    assert(bare.getMessage.contains("translation produced errors"))

    // With it, the whole set translates together and the cross-library define lowers.
    val res = CompileService.compileSql(
      CompileRequest(cql = Some(root), depCql = Seq(dep), target = Some("UsesDep")),
      hasTerminology = false)
    assert(res.defines.contains("UsesDep"), s"expected UsesDep, got ${res.defines}")
  }

  test("a deps array mixes ELM strings and {cql} objects, and an all-string array parses as before") {
    val depElm = CqlToElmCli.translateText(
      "library ElmDep version '1.0.0'\ndefine \"E\": 1\n").elmJson.getOrElse(fail("no ELM"))
    val parsed = CompileRequest.fromJson(
      ujson.Obj("elm" -> "{}",
        "deps" -> ujson.Arr(ujson.Str(depElm), ujson.Obj("cql" -> "library C version '1.0.0'"))).render())
    assert(parsed.deps == Seq(depElm), "ELM string entries belong in `deps`")
    assert(parsed.depCql == Seq("library C version '1.0.0'"), "object entries belong in `depCql`")

    val legacy = CompileRequest.fromJson(
      ujson.Obj("elm" -> "{}", "deps" -> ujson.Arr(ujson.Str(depElm))).render())
    assert(legacy.deps == Seq(depElm) && legacy.depCql.isEmpty)
  }
}
