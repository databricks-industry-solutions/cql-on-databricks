package com.databricks.cql.cqf

import org.opencds.cqf.cql.engine.runtime.Code
import org.opencds.cqf.cql.engine.terminology.{CodeSystemInfo, TerminologyProvider, ValueSetInfo}

import scala.jdk.CollectionConverters._

/** An in-memory `TerminologyProvider` for the ground-truth reference (CONF-GROUND-TRUTH,
  * LANG-TERMINOLOGY). The reference evaluates `code in valueset` via `in(...)`; it reads the SAME
  * hand-authored expansions the engine's broadcast semi-join uses, keyed by value set URL. */
final class InMemoryTerminologyProvider(expansions: Map[String, Set[(String, String)]])
    extends TerminologyProvider {

  override def in(code: Code, valueSet: ValueSetInfo): Boolean =
    expansions.get(valueSet.getId).exists(_.contains((code.getSystem, code.getCode)))

  override def expand(valueSet: ValueSetInfo): java.lang.Iterable[Code] =
    expansions.getOrElse(valueSet.getId, Set.empty).map { case (system, code) =>
      val c = new Code(); c.setSystem(system); c.setCode(code); c
    }.asJava

  override def lookup(code: Code, codeSystem: CodeSystemInfo): Code = null
}
