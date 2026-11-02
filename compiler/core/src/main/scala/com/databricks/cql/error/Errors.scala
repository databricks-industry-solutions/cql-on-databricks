package com.databricks.cql.error

/** The disciplined error model (ARCH-ROBUSTNESS pillar 5).
  *
  * Compile-time failures — unsupported node, type error, unresolved reference, cycle —
  * fail the WHOLE measure, fast and loud. They never degrade into a plausible-but-wrong
  * plan (CORE-NO-SILENT-MISLOWER).
  *
  * Contrast with CQL-defined *runtime* conditions (cardinality violations, invalid unit
  * conversion, division semantics), which become per-row results per spec and never job
  * failures (LANG-RUNTIME-COND, CORE-ISOLATE-ANOMALY) — those are not represented here.
  */
sealed abstract class CompileError(message: String, cause: Throwable = null)
    extends RuntimeException(message, cause)

/** An ELM/kernel node that the engine does not support. Raised at compile time (Lower,
  * or a verifier boundary), NEVER lowered to a guess (ARCH-ROBUSTNESS pillar 1). */
final case class UnsupportedNode(elmType: String, detail: String = "")
    extends CompileError(
      s"Unsupported node '$elmType'${if (detail.nonEmpty) s": $detail" else ""}. " +
        "Failing loudly at compile time rather than emitting a plausible-but-wrong plan " +
        "(CORE-NO-SILENT-MISLOWER).")

/** A reference (define/function/parameter/valueset) that does not resolve. */
final case class UnresolvedReference(kind: String, name: String)
    extends CompileError(s"Unresolved $kind reference: '$name'.")

/** A node could not be assigned a CQL type by the Type/Encoding step — an unmodeled node
  * reached typing, or an operator met a type it cannot accept. Surfaced loudly at the
  * `WellTyped` verifier boundary rather than as a wrong plan (CORE-NO-SILENT-MISLOWER). */
final case class TypeError(detail: String)
    extends CompileError(s"Type error: $detail")

/** A circular reference among defines or functions — CQL forbids both (LANG-FUNCTION).
  * Detected in Resolve and fails the whole measure (ARCH-PIPELINE). */
final case class ReferenceCycle(cycle: Seq[String])
    extends CompileError(s"Circular reference (CQL forbids this): ${cycle.mkString(" -> ")}.")

/** A verifier post-condition was violated between passes (CONF-VERIFIER). Points at the
  * pass boundary that produced the bug, not three passes downstream. */
final case class VerifierError(pass: String, detail: String)
    extends CompileError(s"Verifier failed after pass '$pass': $detail")

/** ELM JSON that could not be lifted into the IR. */
final case class ElmLoadError(detail: String, cause: Throwable = null)
    extends CompileError(s"Failed to load ELM: $detail", cause)

/** A known setting whose value cannot be read. Raised when the setting is resolved, naming the key and the
  * value. The default is never taken in its place (CORE-NO-SILENT-MISLOWER). `expected` says what is accepted. */
final case class ConfigError(key: String, value: String, expected: String)
    extends RuntimeException(s"Config '$key' has the value '$value'; expected $expected.")

/** A retired setting: no lowering reads it, so the request is refused rather than ignored (SVC-PROTOCOL). */
final case class RetiredConfigKey(key: String)
    extends RuntimeException(s"Config key '$key' is retired and no longer accepted. Remove it from the request overrides.")
