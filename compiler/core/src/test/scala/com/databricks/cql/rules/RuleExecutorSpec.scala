package com.databricks.cql.rules

import com.databricks.cql.ir._
import org.junit.runner.RunWith
import org.scalatest.funsuite.AnyFunSuite
import org.scalatestplus.junit.JUnitRunner

/** The Catalyst-style fixpoint runner (ARCH-IR) and its non-termination guard
  * (ARCH-HAZARDS). */
@RunWith(classOf[JUnitRunner])
class RuleExecutorSpec extends AnyFunSuite {

  private def lit(v: String) = Literal("Integer", Some(v))

  test("a node rule fires bottom-up and the batch reaches a fixpoint") {
    val bumpOnes = new NodeRule {
      val name = "BumpOnes"
      val rule: PartialFunction[IrNode, IrNode] = { case Literal("Integer", Some("1")) => lit("2") }
    }
    val exec = new RuleExecutor(Seq(Batch("b", Strategy.FixedPoint(10), bumpOnes)))
    val out = exec.execute(And(lit("1"), Or(lit("1"), lit("3"))))
    assert(out == And(lit("2"), Or(lit("2"), lit("3"))))
  }

  test("an empty batch converges immediately (identity)") {
    val exec = new RuleExecutor(Seq(Batch("noop", Strategy.FixedPoint(10))))
    val in = And(lit("1"), lit("2"))
    assert(exec.execute(in) == in)
  }

  test("a non-converging (oscillating) batch fails loudly, never silently stops") {
    val flip = new NodeRule {
      val name = "Flip"
      val rule: PartialFunction[IrNode, IrNode] = {
        case Literal("Integer", Some("1")) => lit("2")
        case Literal("Integer", Some("2")) => lit("1")
      }
    }
    val exec = new RuleExecutor(Seq(Batch("osc", Strategy.FixedPoint(5), flip)))
    val ex = intercept[IllegalStateException](exec.execute(lit("1")))
    assert(ex.getMessage.contains("did not converge"))
  }

  test("rule firings are logged for plan-diff traceability") {
    var fired = List.empty[String]
    val bump = new NodeRule {
      val name = "Bump"
      val rule: PartialFunction[IrNode, IrNode] = { case Literal("Integer", Some("1")) => lit("2") }
    }
    val exec = new RuleExecutor(Seq(Batch("b", Strategy.FixedPoint(10), bump)),
      onRuleFired = (batch, rule, _) => fired ::= s"$batch/$rule")
    exec.execute(lit("1"))
    assert(fired.contains("b/Bump"))
  }
}
