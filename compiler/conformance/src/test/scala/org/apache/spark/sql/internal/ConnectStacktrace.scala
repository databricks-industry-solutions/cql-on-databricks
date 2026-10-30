package org.apache.spark.sql.internal

/** Phase-8: silence the Spark Connect Scala client's per-operation JVM stack-trace capture.
  *
  * When building a plan the client wraps every `Column`/`Dataset` op in `CurrentOrigin.withOrigin`,
  * which captures a JVM stack trace into the proto's `common.origin.jvm_origin.stack_trace` subtree
  * whenever `SqlApiConf.get.dataFrameQueryContextEnabled` is true. That subtree is pure debug
  * metadata (a call-site breadcrumb), but it dominates the serialized plan — ~80% of the bytes — and
  * counts against the 128 MB gRPC plan limit. The `LoweredTree` render ignores it (it walks only
  * relation nodes), so dropping it at the source changes neither results nor the lowered-tree golden,
  * only the wire size (COMPLEMENTARY to the structural operand-materialization fix, never a substitute).
  *
  * The toggle is read CLIENT-side from the local [[SqlApiConf]] (NOT the remote session), so a builder
  * `.config("spark.sql.dataFrameQueryContext.enabled", "false")` is ignored on a pure Connect client:
  * with no `org.apache.spark.sql.internal.SQLConf` on the classpath, `SqlApiConf.get` resolves to the
  * hardcoded `DefaultSqlApiConf` (capture always on). The only seam is `SqlApiConfHelper.setConfGetter`
  * (a `private[sql]` API — hence this shim lives in the `org.apache.spark.sql.internal` package). We
  * install a getter that delegates everything to `DefaultSqlApiConf` except the one flag we flip off. */
object ConnectStacktrace {

  /** Install (idempotently) a conf getter that disables JVM stack-trace capture during plan building. */
  def disableQueryContextCapture(): Unit =
    SqlApiConfHelper.setConfGetter(() => NoQueryContextConf)

  /** `DefaultSqlApiConf` with `dataFrameQueryContextEnabled` forced off (everything else delegated, so
    * the client behaves exactly as the default getter it replaces). */
  private object NoQueryContextConf extends SqlApiConf {
    private val d = DefaultSqlApiConf
    override def ansiEnabled: Boolean = d.ansiEnabled
    override def caseSensitiveAnalysis: Boolean = d.caseSensitiveAnalysis
    override def maxToStringFields: Int = d.maxToStringFields
    override def setOpsPrecedenceEnforced: Boolean = d.setOpsPrecedenceEnforced
    override def exponentLiteralAsDecimalEnabled: Boolean = d.exponentLiteralAsDecimalEnabled
    override def enforceReservedKeywords: Boolean = d.enforceReservedKeywords
    override def doubleQuotedIdentifiers: Boolean = d.doubleQuotedIdentifiers
    override def timestampType: org.apache.spark.sql.types.AtomicType = d.timestampType
    override def allowNegativeScaleOfDecimalEnabled: Boolean = d.allowNegativeScaleOfDecimalEnabled
    override def charVarcharAsString: Boolean = d.charVarcharAsString
    override def preserveCharVarcharTypeInfo: Boolean = d.preserveCharVarcharTypeInfo
    override def datetimeJava8ApiEnabled: Boolean = d.datetimeJava8ApiEnabled
    override def sessionLocalTimeZone: String = d.sessionLocalTimeZone
    override def legacyTimeParserPolicy: LegacyBehaviorPolicy.Value = d.legacyTimeParserPolicy
    override def stackTracesInDataFrameContext: Int = d.stackTracesInDataFrameContext
    override def dataFrameQueryContextEnabled: Boolean = false // the one flag we flip off
    override def legacyAllowUntypedScalaUDFs: Boolean = d.legacyAllowUntypedScalaUDFs
    // Added in Spark 4.1 (SqlApiConf grew these abstract members) — delegate to the default.
    override def manageParserCaches: Boolean = d.manageParserCaches
    override def parserDfaCacheFlushThreshold: Int = d.parserDfaCacheFlushThreshold
    override def parserDfaCacheFlushRatio: Double = d.parserDfaCacheFlushRatio
    override def legacyParameterSubstitutionConstantsOnly: Boolean = d.legacyParameterSubstitutionConstantsOnly
    override def legacyIdentifierClauseOnly: Boolean = d.legacyIdentifierClauseOnly
  }
}
