package com.databricks.cql.translate

import org.antlr.v4.kotlinruntime.{BaseErrorListener, CharStream, CharStreams, CommonTokenStream, ParserRuleContext,
  RecognitionException, Recognizer}
import org.antlr.v4.kotlinruntime.misc.Interval
import org.cqframework.cql.gen.{cqlLexer, cqlParser}

import scala.collection.mutable
import scala.jdk.CollectionConverters._

/** SVC-NOTEBOOK-SESSION-SCOPE — merge header-less CQL fragments into ONE library, the latest declaration of a
  * name winning. A notebook session's header-less `%%cql` cells and `cql("...")` strings are fragments of
  * the session's library: a later fragment sees an earlier one's defines unqualified, and redeclaring a name
  * replaces it, the way a REPL rebinds a name.
  *
  * The merge is a parse, not a text scan: the cqframework grammar (`library: libraryDefinition? definition*
  * statement* EOF`) splits each fragment into declarations, each keyed by the name it declares. Every kind of
  * named declaration replaces by name — `parameter`, `codesystem`, `valueset`, `code`, `concept` and
  * `define` share the library's one identifier namespace; an `include` replaces by its alias; a `define
  * function` by its name and operand types, so overloads coexist. A `using` is kept as written: an identical
  * repeat is one declaration, and a second VERSION of a model is left in for the translator to reject, as it
  * does for a file on disk ("Could not load model information for model FHIR, version 4.0.0 because version
  * 4.0.1 is already loaded").
  *
  * Each fragment starts in the default context, as a file does: its statements before its first `context`
  * line go before every `context` line of the merged library, where the translator places them in
  * `Unfiltered` (HL7 CQL Author's Guide, Context: "When no context is specified, the default is
  * Unfiltered"). Moving a statement is safe because CQL resolves a define by name wherever it is declared.
  *
  * Comments outside a declaration are not carried; the merged text is the session's working library, not
  * an authored file. */
object CqlFragments {

  /** The merged library, and the expression defines the LAST fragment declares, in its order — what a
    * notebook cell shows as its own result. */
  final case class Merged(cql: String, defines: Seq[String])

  private sealed trait Section
  private case object Head extends Section
  private case object Body extends Section

  /** One declaration: its text as written, where it goes, and (a statement) the context it was written in. */
  private final case class Entry(text: String, section: Section, context: Option[Context])

  /** A `context` line: the name it selects, and the line as written. */
  private final case class Context(name: String, line: String)

  def merge(fragments: Seq[String]): Merged = {
    val entries = mutable.LinkedHashMap.empty[String, Entry]
    var lastDefines = Seq.empty[String]

    def put(key: String, entry: Entry): Unit = {
      // A name that moves between sections (a valueset redeclared as a define) goes to its new section.
      if (entries.get(key).exists(_.section != entry.section)) entries.remove(key)
      entries(key) = entry
    }

    fragments.zipWithIndex.foreach { case (fragment, index) =>
      val (library, input) = parse(fragment, index)
      Option(library.libraryDefinition()).foreach { header =>
        throw new IllegalArgumentException(
          s"fragment ${index + 1} declares `${text(input, header)}`: CQL with a library header is its own " +
            "library and does not join the session library (SVC-NOTEBOOK-SESSION-SCOPE)")
      }

      library.definition().asScala.foreach { d =>
        val decl = text(input, d)
        val key = Option(d.usingDefinition()).map(_ => "using:" + decl.split("\\s+").mkString(" "))
          .orElse(Option(d.includeDefinition()).map { i =>
            "include:" + name(Option(i.localIdentifier()).getOrElse(i.qualifiedIdentifier()))
          })
          .orElse(Option(d.codesystemDefinition()).map(c => "id:" + name(c.identifier())))
          .orElse(Option(d.valuesetDefinition()).map(v => "id:" + name(v.identifier())))
          .orElse(Option(d.codeDefinition()).map(c => "id:" + name(c.identifier())))
          .orElse(Option(d.conceptDefinition()).map(c => "id:" + name(c.identifier())))
          .orElse(Option(d.parameterDefinition()).map(p => "id:" + name(p.identifier())))
          .getOrElse(throw new IllegalArgumentException(s"unrecognized CQL declaration: $decl"))
        put(key, Entry(decl, Head, None))
      }

      var context: Option[Context] = None
      val defines = mutable.ArrayBuffer.empty[String]
      library.statement().asScala.foreach { s =>
        Option(s.contextDefinition()) match {
          case Some(c) =>
            val model = Option(c.modelIdentifier()).map(m => name(m) + ".").getOrElse("")
            context = Some(Context(model + name(c.identifier()), text(input, c)))
          case None =>
            Option(s.expressionDefinition()) match {
              case Some(e) =>
                val defined = name(e.identifier())
                defines += defined
                put("id:" + defined, Entry(text(input, s), Body, context))
              case None =>
                val f = s.functionDefinition()
                val operands = f.operandDefinition().asScala.map(o => o.typeSpecifier().getText).mkString(",")
                put(s"function:${name(f.identifierOrFunctionIdentifier())}($operands)",
                    Entry(text(input, s), Body, context))
            }
        }
      }
      lastDefines = defines.distinct.toSeq
    }

    val head = entries.values.filter(_.section == Head).map(_.text)
    val body = entries.values.filter(_.section == Body).toSeq
    // Default-context statements first, then each written context in order, a `context` line at each change.
    val (preamble, scoped) = body.partition(_.context.isEmpty)
    val lines = mutable.ArrayBuffer.empty[String]
    lines ++= head
    lines ++= preamble.map(_.text)
    var current: Option[String] = None
    scoped.foreach { e =>
      val ctx = e.context.get
      if (!current.contains(ctx.name)) { lines += ctx.line; current = Some(ctx.name) }
      lines += e.text
    }
    Merged(lines.mkString("", "\n", "\n"), lastDefines)
  }

  /** Parse one fragment, failing loudly on a syntax error rather than merging what the parser recovered. */
  private def parse(fragment: String, index: Int): (cqlParser.LibraryContext, CharStream) = {
    val errors = mutable.ArrayBuffer.empty[String]
    val listener = new BaseErrorListener {
      override def syntaxError(recognizer: Recognizer[_, _], offendingSymbol: Object, line: Int,
                               charPositionInLine: Int, msg: String, e: RecognitionException): Unit =
        errors += s"$line:$charPositionInLine $msg"
    }
    val input = CharStreams.INSTANCE.fromString(fragment)
    val lexer = new cqlLexer(input)
    lexer.removeErrorListeners()
    lexer.addErrorListener(listener)
    val parser = new cqlParser(new CommonTokenStream(lexer))
    parser.removeErrorListeners()
    parser.addErrorListener(listener)
    val library = parser.library()
    if (errors.nonEmpty)
      throw new IllegalArgumentException(s"fragment ${index + 1} is not valid CQL: ${errors.mkString("; ")}")
    (library, input)
  }

  /** The declaration's source text as written. Read through the char stream, whose indices are code points. */
  private def text(input: CharStream, ctx: ParserRuleContext): String =
    input.getText(new Interval(ctx.getStart.getStartIndex, ctx.getStop.getStopIndex))

  /** An identifier's name, without the `"…"` or `` `…` `` that delimits it. */
  private def name(ctx: ParserRuleContext): String = {
    val raw = ctx.getText
    if (raw.length >= 2 && (raw.head == '"' || raw.head == '`') && raw.last == raw.head) raw.substring(1, raw.length - 1)
    else raw
  }
}
