package com.databricks.cql.translate

import kotlinx.io.Source
import org.cqframework.cql.cql2elm.{CqlIncludeException, LibraryContentType, LibrarySourceProvider,
  StringLibrarySourceProvider}
import org.hl7.elm.r1.VersionedIdentifier

import scala.jdk.CollectionConverters._

/** LANG-FHIRHELPERS-FROM-CONTENT — the library sources a translation is given, and nothing else.
  *
  * An include resolves to the supplied source that declares its name and version (cqframework's
  * `StringLibrarySourceProvider` does the matching). An include no supplied source declares fails the
  * translation here, naming the library and version.
  *
  * Failing here is what makes the supplied sources the only path. cqframework's `DefaultLibrarySourceLoader`
  * asks its providers in registration order, and on its first lookup it appends every provider registered
  * through `META-INF/services`. The `org.cqframework:quick` jar registers one, `FhirLibrarySourceProvider`,
  * which serves FHIRHelpers 1.0.2 to 4.0.1 from the jar. A provider that returned null for a missing
  * library would let the loader fall through to that copy. This one is registered first and throws, so the
  * loader never reaches it.
  *
  * Only CQL content is answered. ELM content (JSON, XML) is never supplied, so those lookups return null, as
  * `StringLibrarySourceProvider` does, and translation falls back to the CQL. */
final class SuppliedLibrarySourceProvider(sources: Seq[String]) extends LibrarySourceProvider {

  private val supplied = new StringLibrarySourceProvider(sources.asJava)

  override def getLibrarySource(id: VersionedIdentifier): Source =
    Option(supplied.getLibrarySource(id)).getOrElse(throw new CqlIncludeException(
      s"Library ${id.getId} version '${Option(id.getVersion).getOrElse("")}' is not among the supplied library " +
        "sources. Supply its CQL with the library that includes it.",
      id.getSystem, id.getId, id.getVersion, null))

  override def getLibraryContent(id: VersionedIdentifier, contentType: LibraryContentType): Source =
    if (contentType == LibraryContentType.CQL) getLibrarySource(id) else null
}
