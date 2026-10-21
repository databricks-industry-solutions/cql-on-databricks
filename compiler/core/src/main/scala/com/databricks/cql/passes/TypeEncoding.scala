package com.databricks.cql.passes

import com.databricks.cql.error.TypeError
import com.databricks.cql.ir.CqlType._
import com.databricks.cql.ir._
import com.databricks.cql.lower.Encoding
import com.databricks.cql.sema.FhirModel

/** The Type/Encoding step (ARCH-TYPE-ATTR, ARCH-PIPELINE, `engine-design.md` section 2.1): the
  * result type and physical encoding of every kernel node.
  *
  * It is a RECOMPUTABLE synthesized attribute, not state stored on the IR. `typeOf` derives
  * a node's [[CqlType]] bottom-up on demand — leaves seed from declared/parsed types and the
  * inherited [[TypeCtx]] (parameter types, define result types, query-alias element types);
  * every other node folds its children. Because nothing is cached on the tree, typing
  * survives Normalize rewrites (inlining, desugaring) for free, exactly as Catalyst's
  * `dataType` does (ARCH-IR). The design requires this run before rewrite/lowering so
  * type-directed transforms (ARCH-CONDITIONAL section 6) and Phase 3 interval/quantity lowering can
  * consume it.
  *
  * The `WellTyped` verifier asserts totality at the pass boundary (every node types without
  * throwing); the loud failure for a genuinely untypeable node is [[TypeError]]. In this
  * foundation typing is conservative — it returns `AnyType` where precise typing needs model
  * info a later phase supplies (FHIR property navigation), rather than guessing — and gets
  * stricter as Phase 3 enriches the temporal/quantity families (ARCH-SEAMS).
  */
object TypeEncoding {

  /** Inherited typing context (ARCH-ENV inherited attributes): declared parameter types,
    * the library's define bodies (for `ExpressionRef` result typing), and the query-alias
    * element-type scope that grows as `typeOf` descends into a query. */
  final case class TypeCtx(
      params: Map[String, CqlType],
      defines: Map[String, Expr],
      aliases: Map[String, CqlType] = Map.empty) {
    def withAlias(name: String, t: CqlType): TypeCtx = copy(aliases = aliases + (name -> t))
  }

  /** Build the typing context from a resolved library: parameter types from their
    * declarations, define bodies by name. */
  def ctxOf(lib: Library): TypeCtx = {
    val params = lib.parameters.map { p =>
      p.name -> p.parameterTypeName.fold(AnyType: CqlType)(CqlType.parse)
    }.toMap
    val defines = lib.statements.collect { case e: ExpressionDef => e.name -> e.expression }.toMap
    TypeCtx(params, defines)
  }

  /** The result type of an expression under a typing context. Total over the kernel:
    * throws [[TypeError]] only for an unmodeled node (which the OnlyKernelNodes verifier
    * should already have rejected). */
  def typeOf(expr: Expr, ctx: TypeCtx): CqlType = infer(expr, ctx, Set.empty)

  /** The physical encoding implied by a type (DATA-LIST-ENCODING): **`Relation` is the default for
    * lists** (rows + `__subject_id` + `__ordinal`), because filtering/projection/existence/
    * membership/set-ops/aggregates are all native set operations there. Everything else is scalar.
    *
    * The `Array` boxing (`collect_list`) is forced only at specific BOUNDARIES — the output/export
    * boundary, a list that is a tuple/struct field, a nested `List<List<T>>` (inner list pinned to
    * `Array`), and whole-list operators (`Count`/`Indexer`/list `=`/`~`/order-or-position-dependent
    * ops). Those are CONTEXTUAL lowering-site decisions (the box is inserted where the value flows
    * into such a position), not properties of the type alone, so they are realized in the lowering
    * (`FrameLowering`/`QueryLowering.collectList`), not encoded here. A list-valued define therefore
    * materializes as a `Relation` frame (DATA-DEFINE-DF), boxed to `Array` only at those boundaries. */
  def encodingOf(t: CqlType): Encoding = t match {
    case _: ListType => Encoding.Relation
    case _           => Encoding.Scalar
  }

  // `visiting` guards `ExpressionRef` chains against runaway recursion; Resolve already
  // rejects true cycles (DefineGraph), so this only bounds the walk defensively.
  private def infer(expr: Expr, ctx: TypeCtx, visiting: Set[String]): CqlType = {
    def go(e: Expr): CqlType = infer(e, ctx, visiting)

    expr match {
      // -- Leaves --
      case NullExpr()             => AnyType            // null is typeless in CQL; refined by context
      case Literal(vt, _)         => CqlType.parse(vt)
      case QuantityExpr(_, unit)  => QuantityType(unit)
      // Date/DateTime carry the precision implied by their component count (LANG-INTERVAL).
      case DateExpr(parts)        => DateType(Some(DateTimePrecision.ofComponentCount(parts.size)))
      case DateTimeExpr(parts)    => DateTimeType(Some(DateTimePrecision.ofComponentCount(parts.size)))
      // Time components start at Hour (rank 3): hour-only → Hour, +min → Minute, … +ms → Millisecond.
      case TimeExpr(parts)        =>
        TimeType(Some(DateTimePrecision.ordered(math.min(parts.size + 2, DateTimePrecision.Millisecond.rank))))
      case _: RatioExpr           => RatioType
      // ELM `Instance` — a typed constructor; its System/class type is the declared classType
      // (`{urn:hl7-org:elm-types:r1}Quantity` → QuantityType, etc.), so quantity/ratio ops route on it.
      case InstanceExpr(ct, _)    => CqlType.parse(ct)
      case ConvertQuantity(_, u)  => QuantityType(Some(u))  // result is a Quantity in the target UCUM unit
      // A bare identifier reference; normally rewritten to a scope-rooted Property before typing
      // (IdentifierRefResolver). Typeless as a residual leaf — refined by context, like NullExpr.
      case _: IdentifierRef       => AnyType

      case ExpressionRef(name, None) =>
        ctx.defines.get(name) match {
          case Some(_) if visiting.contains(name) => AnyType
          case Some(body)                         => infer(body, ctx, visiting + name)
          case None                               => AnyType // cross-library / external
        }
      case _: ExpressionRef       => AnyType
      case ParameterRef(name, None) => ctx.params.getOrElse(name, AnyType)
      case _: ParameterRef        => AnyType
      case AliasRef(name)         => ctx.aliases.getOrElse(name, AnyType)
      case QueryLetRef(name)      => ctx.aliases.getOrElse(name, AnyType) // let/aggregate-accumulator scope
      case _: OperandRef          => AnyType            // bound only inside an un-inlined function body
      case _: ValueSetRef         => NamedType("ValueSet")
      case _: CodeSystemRef       => NamedType("CodeSystem")
      case _: CodeRef             => NamedType("Code")
      case _: ConceptRef          => NamedType("Concept")

      // -- Boolean-valued operators --
      case _: And | _: Or | _: Xor | _: Implies | _: Not                  => BooleanType
      case _: Equal | _: NotEqual | _: Equivalent                         => BooleanType
      case _: Less | _: Greater | _: LessOrEqual | _: GreaterOrEqual      => BooleanType
      case _: IsNull | _: Exists | _: In | _: InValueSet | _: Is          => BooleanType
      case _: IsTrue | _: IsFalse                                         => BooleanType
      // Interval (Allen-relation) operators are boolean-valued (LANG-INTERVAL).
      case _: Before | _: After | _: Overlaps                             => BooleanType
      case _: Includes | _: IncludedIn | _: Starts | _: Ends              => BooleanType
      case _: SameAs | _: SameOrBefore | _: SameOrAfter                   => BooleanType

      // -- Arithmetic. CQL `/` is always Decimal (CQL Reference section Decimal); the others widen.
      //    Quantity arithmetic keeps the (left) operand's unit (LANG-QUANTITY). --
      case Add(a, b)              => arith(go(a), go(b))
      case Subtract(a, b)         => arith(go(a), go(b))
      case Multiply(a, b)         => arith(go(a), go(b))
      case Modulo(a, b)           => arith(go(a), go(b))
      case TruncatedDivide(a, b)  => arith(go(a), go(b))
      case Power(a, b)            => arith(go(a), go(b))
      case Negate(o)              => go(o)
      // Quantity ÷ scalar keeps the unit; quantity ÷ quantity is the dimensionless ratio (unit '1');
      // scalar ÷ scalar is Decimal (LANG-QUANTITY).
      case Divide(a, b)           => (go(a), go(b)) match {
        case (qa: QuantityType, _: QuantityType) => QuantityType(Some("1"))
        case (qa: QuantityType, _)               => qa
        case _                                   => DecimalType
      }
      // Unary numeric functions: Abs/Predecessor/Successor preserve the operand
      // type; Floor/Ceiling/Truncate return Integer; Round returns Decimal.
      case Abs(o)                 => go(o)
      case Predecessor(o)         => go(o)
      case Successor(o)           => go(o)
      case _: Floor | _: Ceiling | _: Truncate => IntegerType
      case _: Precision           => IntegerType // digit-count of a Decimal / Date / DateTime / Time
      // LowBoundary/HighBoundary preserve the value operand's type (the boundary of a Decimal is a
      // Decimal; of a Date a Date; etc.).
      case LowBoundary(v, _)      => go(v)
      case HighBoundary(v, _)     => go(v)
      case _: Round               => DecimalType
      case _: Ln | _: Exp | _: Log => DecimalType
      case MinValue(vt)           => CqlType.parse(vt)
      case MaxValue(vt)           => CqlType.parse(vt)

      // -- Conditionals: result is the join of the branch types --
      // Coalesce(List<T>) (the list overload) yields the element type; variadic Coalesce joins its args.
      case Coalesce(Seq(single)) if go(single).isInstanceOf[ListType] => elementOf(go(single))
      case Coalesce(os)           => os.map(go).reduceOption(CqlType.join).getOrElse(AnyType)
      case If(_, t, e)            => CqlType.join(go(t), go(e))
      case Case(_, items, els)    => (items.map(i => go(i.thenExpr)) :+ go(els)).reduce(CqlType.join)

      // -- Lists & intervals --
      case ListExpr(elems)        => ListType(elems.map(go).reduceOption(CqlType.join).getOrElse(AnyType))
      case First(o)               => elementOf(go(o))
      case Last(o)                => elementOf(go(o))
      case SingletonFrom(o)       => elementOf(go(o))
      case ToList(o)              => ListType(go(o)) // singleton → list (null → empty list)
      case Indexer(list, _)       => elementOf(go(list))
      case Distinct(o)            => go(o)   // dedupe preserves the list type (LANG-EQUIV)
      case Count(_)               => IntegerType
      case _: Split               => ListType(StringType) // split a string -> List<String>
      // List set operators yield a list whose element type unifies the two operands' elements.
      case Union(a, b)            => ListType(CqlType.join(elementOf(go(a)), elementOf(go(b))))
      case Intersect(a, b)        => ListType(CqlType.join(elementOf(go(a)), elementOf(go(b))))
      case Except(a, b)           => ListType(CqlType.join(elementOf(go(a)), elementOf(go(b))))
      case IntervalExpr(low, high, _, _) =>
        IntervalType((low.toSeq ++ high.toSeq).map(go).reduceOption(CqlType.join).getOrElse(AnyType))
      // Interval accessors: start/end yield the point type; width yields the point's
      // difference type (Integer for an integer interval — CQL Reference) (LANG-INTERVAL).
      case Start(o)               => intervalPointOf(go(o))
      case End(o)                 => intervalPointOf(go(o))
      case Width(o)               => intervalPointOf(go(o))
      // Temporal conversions (LANG-INTERVAL): DateFrom → Date, ToDateTime → DateTime; Message
      // returns its source unchanged; CalculateAgeAt is an Integer count of calendar units.
      case _: DateFrom            => DateType(Some(DateTimePrecision.Day))
      case _: ToDateTimeExpr      => DateTimeType()
      case Message(o)             => go(o)
      case _: CalculateAgeAt      => IntegerType
      // Code->Concept promotion and its resolved literal (LANG-TERMINOLOGY).
      case _: ToConcept | _: ConceptValue => NamedType("Concept")

      // -- Type conversions carry their declared target type. An As to a ChoiceTypeSpecifier
      //    CONSTRUCTS a choice (LANG-CHOICE); a named As projects to that type. --
      case Convert(_, toType)            => CqlType.parse(toType)
      // As-to-choice: a list operand makes it List<Choice> (e.g. `union` of mixed lists), else a scalar choice.
      case As(o, _, _, cts) if cts.nonEmpty =>
        val choice = ChoiceType(cts.map(CqlType.parse))
        go(o) match { case _: ListType => ListType(choice); case _ => choice }
      case As(_, asType, _, _)           => CqlType.parse(asType)

      // -- Tuple (LANG-QUERY-MULTISOURCE): a record of named slots, each typed from its value. --
      case TupleExpr(elems)       => TupleType(elems.map(e => e.name -> go(e.value)))

      // -- Property navigation. Over a tuple it projects the named slot's type; over a FHIR model
      //    type precise typing needs model info, so it stays AnyType (Phase 2/3 refinement). The
      //    navigated source is either an explicit `source` expr or a `scope` alias in scope. --
      case Property(src, scope, path) =>
        val srcT = src.map(go).orElse(scope.flatMap(ctx.aliases.get)).getOrElse(AnyType)
        srcT match {
          case TupleType(es)                              => es.toMap.getOrElse(path, AnyType)
          // An interval's `low`/`high` are its point type (CQL spec, Interval Operators: the bounds),
          // so `(x as Interval<Quantity>).low` is a Quantity and date arithmetic on it lowers as such.
          case IntervalType(p) if path == "low" || path == "high" => p
          // The FHIRHelpers `.value` primitive unwrap preserves a temporal source's type (so
          // `Patient.birthDate.value` stays a Date through the unwrap).
          case t if path == "value" && isTemporal(t)      => t
          // Precise FHIR navigation (TYPE-FHIR-MODEL): a modeled resource/datatype element types
          // from the static model. A `value[x]` choice base or an unmodeled element falls through to
          // `AnyType` — the conservative don't-guess policy (the `As`/`Is` value[x] path handles it).
          case NamedType(n)                               => FhirModel.navType(n, path).getOrElse(AnyType)
          // Navigation off a het-Choice union source (`([ServiceRequest] union [MedicationRequest]) X`
          // then `X.reasonCode`): dispatch the path into each modeled member and JOIN the per-arm
          // results. When every arm's element is 0..* (e.g. `reasonCode: List<CodeableConcept>`) the
          // join stays a `ListType`, so downstream list-cardinality reads (value-set membership's
          // list-explode, `isListElement`) see the list level the arms all share (TYPE-FHIR-MODEL).
          case ChoiceType(ms)                             =>
            ms.collect { case NamedType(n) => FhirModel.navType(n, path) }.flatten
              .reduceOption(CqlType.join).getOrElse(AnyType)
          // Unmodeled SOURCE (`AnyType`, e.g. a context `ExpressionRef` with no resolvable body):
          // the model-derived, conflict-free temporal-leaf fallback (supersedes the old hand
          // `DateElements`/`DateTimeElements` lists), else `AnyType`.
          case _                                          => FhirModel.temporalLeaf.getOrElse(path, AnyType)
        }

      // -- Data access: a retrieve yields a list of the named resource type --
      case Retrieve(dataType, _, _, _, _, _, _, _) => ListType(NamedType(dataType))

      // -- Query: bind each source alias to its element type, then type the result shape. An
      //    `aggregate` yields the accumulator type (scalar); otherwise a list of the return shape. --
      case q: Query =>
        val boundSrc = q.sources.foldLeft(ctx) { (c, s) =>
          c.withAlias(s.alias, elementOf(infer(s.expr, c, visiting)))
        }
        // `let` bindings are query-iteration-scoped VALUES (not list elements): each sees the source
        // aliases + earlier lets, so type them in order into the scope (LANG-QUERY-LET).
        val bound = q.lets.foldLeft(boundSrc) { (c, l) =>
          c.withAlias(l.identifier, infer(l.expr, c, visiting))
        }
        q.aggregate match {
          case Some(a) =>
            val startT = infer(a.starting, ctx, visiting)
            CqlType.join(startT, infer(a.body, bound.withAlias(a.identifier, startT), visiting))
          case None =>
            val elemT = q.returnExpr match {
              case Some(r)                     => infer(r, bound, visiting)
              case None if q.sources.size == 1 => elementOf(infer(q.sources.head.expr, ctx, visiting))
              case None                        => AnyType // multi-source implicit tuple
            }
            ListType(elemT)
        }

      // -- FHIRHelpers temporal converters carry a CQL temporal type so the interval/precision
      //    machinery engages over FHIR retrieve dates (LANG-INTERVAL); the nominal precision is
      //    DateTime=finest, Date=day (per-row precision from partial FHIR dates is deferred). --
      case FunctionRef("ToDateTime", Some("FHIRHelpers"), _) => DateTimeType()
      case FunctionRef("ToDate", Some("FHIRHelpers"), _)     => DateType(Some(DateTimePrecision.Day))
      // A FHIR Period converts to an Interval<DateTime> (its start/end are dateTimes), so the
      // Allen-relation algebra engages over a Period-typed `where` (LANG-INTERVAL).
      case FunctionRef("ToInterval", Some("FHIRHelpers"), _) => IntervalType(DateTimeType())
      // A FHIR Quantity/Age/SimpleQuantity converts to a System Quantity (a Range's low/high bounds,
      // for the value[x] `Interval<Quantity>` branch of `toInterval`); typed so the enclosing interval
      // is `Interval<Quantity>` (LANG-QUANTITY).
      case FunctionRef("ToQuantity", Some("FHIRHelpers"), _) => QuantityType(None)
      // -- Other user functions are inlined in Normalize; cross-library (FHIRHelpers) calls reaching
      //    here are conservatively `AnyType` until structural converters are typed. --
      case _: FunctionRef         => AnyType

      case u: UnknownExpr =>
        throw TypeError(
          s"cannot type unmodeled node '${u.elmType}' " +
            "(should have been rejected by the OnlyKernelNodes verifier)")
    }
  }

  private def elementOf(t: CqlType): CqlType = t match {
    case ListType(e) => e
    case _           => AnyType
  }

  private def intervalPointOf(t: CqlType): CqlType = t match {
    case IntervalType(p) => p
    case _               => AnyType
  }

  /** The result type of a numeric/quantity binary op: a quantity operand makes the result a
    * quantity carrying the (left-preferred) unit (LANG-QUANTITY); otherwise numerics widen via
    * [[CqlType.join]]. */
  private def arith(a: CqlType, b: CqlType): CqlType = (a, b) match {
    // A temporal operand dominates: `Date/DateTime ± <duration>` is that temporal type, not a quantity
    // (LANG-INTERVAL) — so a chained `birthDate + age + 1 year` stays temporal through each step.
    case (ta, _) if isTemporal(ta) => ta
    case (_, tb) if isTemporal(tb) => tb
    case (qa: QuantityType, _) => qa
    case (_, qb: QuantityType) => qb
    case (ta, tb)              => CqlType.join(ta, tb)
  }

  private def isTemporal(t: CqlType): Boolean = t match {
    case _: DateType | _: DateTimeType | _: TimeType => true
    case _                                           => false
  }
}
