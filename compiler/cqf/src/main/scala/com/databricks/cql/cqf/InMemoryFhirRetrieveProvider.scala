package com.databricks.cql.cqf

import org.hl7.fhir.instance.model.api.IBaseResource
import org.hl7.fhir.r4.model.{CodeableConcept, Patient, Reference}

import scala.util.Try
import org.opencds.cqf.cql.engine.retrieve.RetrieveProvider
import org.opencds.cqf.cql.engine.runtime.{Code, Interval}

import scala.jdk.CollectionConverters._

/** An in-memory FHIR `RetrieveProvider` for the ground-truth reference (CONF-GROUND-TRUTH): it
  * serves the SAME hand-authored sample resources the engine reads, filtered to the Patient
  * context and (when a value set is supplied) to value-set membership. This is the test-only Java
  * replacement for the JS stack's `cql-exec-fhir`; the REST/search providers in
  * `engine-fhir` need a live FHIR server, which a hermetic differential must not depend on.
  *
  * Per the cqframework contract the provider applies the code filter itself (it receives
  * `valueSet`/`codes`/`codePath`), so a value-set OR single-code retrieve returns only matching
  * resources — exactly what the engine's broadcast semi-join / `codeFilter` does on its side.
  * `expansions` maps a value set URL to its `(system, code)` set, the SAME expansion the engine's
  * TerminologyProvider uses.
  *
  * Resolve `Patient` by id, others by `subject`/`patient`/`beneficiary` reference id; value-set membership over
  * the codings of the resource's `codePath` CodeableConcept(s) — `code` for an Observation/Condition,
  * `type` for an Encounter, etc. — read generically so any resource type filters correctly. */
final class InMemoryFhirRetrieveProvider(
    resources: Seq[IBaseResource],
    expansions: Map[String, Set[(String, String)]] = Map.empty) extends RetrieveProvider {

  // PERF (scale harness): index the resources ONCE at construction so `retrieve` is an O(1) hashmap
  // lookup + a filter over the SMALL matched set — NOT an O(all-resources) scan on every per-subject
  // call. The old linear scan made a full-population evaluation O(patients^2) (each of N subjects
  // re-scanned all N·k resources), which dominated the reference's timing at scale and made it look far
  // slower than the CQF engine actually is. Semantics are IDENTICAL to that scan: patient-scoped
  // resources match by subject id; context-less resources (no subject/patient ref, e.g. Medication —
  // reached by a reference follow, LANG-REFERENCE-FOLLOW) are returned for EVERY subject; a null context
  // returns all of the type. The index build is O(resources) — like the JSON parse in `buildProviders`
  // and Spark's own cold read/shuffle, it is part of the honest per-run cost (inside the timed region),
  // not hidden: this fixes an accidental quadratic, it does not skip a logical step.
  private val subjectKeyOf: IBaseResource => Option[String] = {
    case p: Patient => Option(p.getIdElement.getIdPart)
    case r          => subjectReferenceId(r) // reflection, computed ONCE per resource here (not per retrieve)
  }
  private val keyed: Seq[(IBaseResource, Option[String])] = resources.map(r => (r, subjectKeyOf(r)))
  private val byType: Map[String, Seq[IBaseResource]] = resources.groupBy(_.fhirType)
  private val bySubject: Map[(String, String), Seq[IBaseResource]] =
    keyed.collect { case (r, Some(id)) => (r.fhirType, id) -> r }
      .groupBy(_._1).map { case (k, v) => k -> v.map(_._2) }
  private val contextlessByType: Map[String, Seq[IBaseResource]] =
    keyed.collect { case (r, None) => r }.groupBy(_.fhirType)

  override def retrieve(
      context: String, contextPath: String, contextValue: Object,
      dataType: String, templateId: String, codePath: String,
      codes: java.lang.Iterable[Code], valueSet: String,
      datePath: String, dateLowPath: String, dateHighPath: String,
      dateRange: Interval): java.lang.Iterable[Object] = {

    val subject = Option(contextValue).map(_.toString)
    val vs = Option(valueSet).flatMap(expansions.get)
    // A retrieve filtered by a declared `code` (`[ObservationScreeningAssessment: "<code>"]`) passes its
    // `(system, code)` set via `codes` rather than `valueSet`; apply it the same way (CQL retrieve code
    // filter, LANG-TERMINOLOGY) so the reference matches a single-code retrieve to the engine's `codeFilter`.
    val codeSet = Option(codes).map(_.asScala.iterator.map(c => (c.getSystem, c.getCode)).toSet).filter(_.nonEmpty)

    // Context match by index lookup (was `contextMatches`): patient-scoped resources for this subject PLUS
    // the type's context-less resources; a null subject (Unfiltered) returns every resource of the type.
    val candidates = subject match {
      case Some(id) => bySubject.getOrElse((dataType, id), Nil) ++ contextlessByType.getOrElse(dataType, Nil)
      case None     => byType.getOrElse(dataType, Nil)
    }
    candidates.iterator.filter { r =>
      profileMatches(r, Option(templateId)) &&
        valueSetMatches(r, vs, Option(codePath)) &&
        valueSetMatches(r, codeSet, Option(codePath))
    }.map(_.asInstanceOf[Object]).toSeq.asJava
  }

  /** True when the retrieve declares no profile, or the resource's `meta.profile` contains it.
    *
    * The cqframework contract passes the retrieve's PROFILE canonical in `templateId` — a QI-Core /
    * US-Core profile-specific type (`[USCoreBMIProfile]`, `[ConditionEncounterDiagnosis]`,
    * `[ObservationCancelled]`) narrows to resources CONFORMING to that profile, not merely to the base
    * FHIR type. Ignoring it is only invisible while every profile-typed retrieve ALSO carries a code
    * filter that happens to separate the profiles. A profile-only retrieve — e.g.
    * `[USCoreObservationOccupationProfile]`, no codes — breaks that assumption: without this filter it
    * returns EVERY `Observation` for the subject. A measure then evaluating `O.value in "<VS>"` over
    * observations of another profile whose `value[x]` is a `Quantity` makes the reference die with
    * `Could not resolve call to operator 'ToConcept(Quantity)'` — dropping the whole subject from the
    * differential and the answer-key comparison (36 of 62 patients in one measured corpus).
    *
    * Skipped for a type this fixture does not profile-tag at all (see [[profileTaggedTypes]]). */
  private def profileMatches(r: IBaseResource, templateId: Option[String]): Boolean =
    templateId match {
      case Some(t) if !isBaseStructureDefinition(t, r.fhirType) =>
        val claimed = profilesOf(r)
        claimed.isEmpty || claimed.contains(t) // untagged resource: cannot disprove conformance
      case _ => true
    }

  /** True for the BASE `StructureDefinition/<ResourceType>` canonical. Every retrieve carries a
    * `templateId`, and for a plain `using FHIR` library that canonical IS the base definition — which
    * real data never lists in `meta.profile` (that array claims SPECIALISED profiles). Treating it as a
    * profile constraint would drop every row, so conformance is taken as implied by `resourceType`. */
  private def isBaseStructureDefinition(templateId: String, fhirType: String): Boolean =
    templateId == s"http://hl7.org/fhir/StructureDefinition/$fhirType"

  /** The Patient-context key of a resource (DATA-SUBJECT-KEY): its `subject`, `patient` or `beneficiary`
    * (`Coverage`) reference id, in that order — the same fields the client's reshape reads — found
    * generically so any Patient-context resource type filters correctly, not just Condition. */
  private def subjectReferenceId(r: IBaseResource): Option[String] = {
    def ref(method: String): Option[Reference] =
      Try(r.getClass.getMethod(method).invoke(r).asInstanceOf[Reference]).toOption.filter(_ != null)
    ref("getSubject").orElse(ref("getPatient")).orElse(ref("getBeneficiary")).flatMap(referenceId)
  }

  /** True when no value set is requested, or any coding of the resource's `codePath`
    * CodeableConcept(s) is in the expansion — the SAME membership the engine's broadcast semi-join
    * applies on the retrieve's `codeProperty`. */
  private def valueSetMatches(r: IBaseResource, vs: Option[Set[(String, String)]],
                              codePath: Option[String]): Boolean =
    vs match {
      case None        => true
      case Some(codes) =>
        val path = codePath.getOrElse("code")
        codeableConcepts(r, path).exists(_.getCoding.asScala.exists(co =>
          codes.contains((co.getSystem, co.getCode))))
    }

  /** The CodeableConcept(s) at `path` on a resource, found generically (a single CodeableConcept
    * like `Observation.code`, or a list like `Encounter.type`). */
  private def codeableConcepts(r: IBaseResource, path: String): Seq[CodeableConcept] = {
    val getter = "get" + path.headOption.map(_.toUpper).getOrElse(' ') + path.drop(1)
    Try(r.getClass.getMethod(getter).invoke(r)).toOption.toSeq.flatMap {
      case cc: CodeableConcept             => Seq(cc)
      case list: java.util.List[_]         => list.asScala.collect { case cc: CodeableConcept => cc }.toSeq
      case _                               => Seq.empty
    }
  }

  private def referenceId(ref: Reference): Option[String] =
    Option(ref).flatMap(r => Option(r.getReference)).map(_.split("/").last)

  /** The canonical profile URLs a resource claims in `meta.profile` (empty when untagged). */
  private def profilesOf(r: IBaseResource): Set[String] =
    Option(r.getMeta).toSeq.flatMap(m =>
      Option(m.getProfile).toSeq.flatMap(_.asScala.flatMap(p => Option(p.getValueAsString)))).toSet
}
