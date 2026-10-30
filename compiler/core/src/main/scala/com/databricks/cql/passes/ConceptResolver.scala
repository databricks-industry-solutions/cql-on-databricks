package com.databricks.cql.passes

import com.databricks.cql.ir._

/** Normalize step: resolve CQL terminology promotions to a literal [[ConceptValue]] (LANG-TERMINOLOGY).
  *
  * A measure compares a FHIR CodeableConcept to a declared code/concept with `~`, e.g.
  * `condition.clinicalStatus ~ "active"` or `verificationStatus ~ "confirmed"`. The translator emits
  * the right-hand code as `ToConcept(CodeRef("active"))` (the implicit Code->Concept promotion). The
  * code/concept declarations (`CodeDef`/`CodeSystemDef`/`ConceptDef`) are compile-time constants in the
  * library, so this pass resolves each `ToConcept(...)` / standalone `ConceptRef` to the literal
  * `(codeSystemUrl, code)` set it denotes — a self-contained kernel node lowering can test against a
  * CodeableConcept's `.coding` array with NO runtime terminology lookup or env plumbing.
  *
  * Bare `CodeRef`s are left untouched: a code only reaches lowering as a `~`/`in` operand wrapped in
  * `ToConcept` in these measures, while a `CodeRef` can also appear as a `Retrieve` code filter where it
  * must stay a reference. Runs after function inlining so codes inside inlined fluent-function bodies
  * (a helper library's status filters and prevalence interval) are resolved too. */
object ConceptResolver {

  def normalize(lib: Library): Library = {
    val csUrl: Map[String, String] = lib.codeSystems.map(cs => cs.name -> cs.id).toMap
    // A code resolves to (codeSystemUrl, code); the CodeDef `id` IS the code, `codeSystem` its name.
    val codePair: Map[String, (String, String)] =
      lib.codes.map(cd => cd.name -> (cd.codeSystem.flatMap(csUrl.get).getOrElse(""), cd.id)).toMap
    val conceptPairs: Map[String, Seq[(String, String)]] =
      lib.concepts.map(cn => cn.name -> cn.codeNames.flatMap(codePair.get)).toMap

    /** The literal concept a (possibly `ToConcept`-wrapped) code/concept reference denotes, if known. */
    def resolved(e: Expr): Option[ConceptValue] = e match {
      case CodeRef(n, _)    => codePair.get(n).map(p => ConceptValue(Seq(p)))
      case ConceptRef(n, _) => conceptPairs.get(n).map(ConceptValue(_))
      case ToConcept(inner) => resolved(inner)
      case cv: ConceptValue => Some(cv)
      case _                => None
    }

    // A code/concept reference in `~`/`!~` OPERAND position resolves to its literal ConceptValue —
    // including a BARE `CodeRef` (`Encounter.class !~ "virtual"`), which is safe here (unlike a
    // `Retrieve` code filter) because it is an equivalence operand, not a scan-filter reference.
    def rwCodeOperand(x: Expr): Expr = x match {
      case _: CodeRef | _: ConceptRef | _: ToConcept if resolved(x).isDefined => resolved(x).get
      case _                                                                  => rw(x)
    }

    def rw(e: Expr): Expr = e match {
      case (_: ToConcept | _: ConceptRef) if resolved(e).isDefined => resolved(e).get
      case Equivalent(l, r)                                        => Equivalent(rwCodeOperand(l), rwCodeOperand(r))
      case other =>
        other.withNewChildren(other.children.map { case c: Expr => rw(c); case c => c }).asInstanceOf[Expr]
    }

    lib.copy(statements = lib.statements.map {
      case e: ExpressionDef => e.copy(expression = rw(e.expression))
      case f: FunctionDef   => f.copy(body = rw(f.body))
      case other            => other
    })
  }
}
