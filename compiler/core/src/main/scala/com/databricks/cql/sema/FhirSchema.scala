package com.databricks.cql.sema

import com.databricks.cql.sema.FhirShape.{Leaf, Shape, Struct}
import org.apache.spark.sql.types._

import java.util.concurrent.ConcurrentHashMap

/** The RAW physical Spark schema of a FHIR resource (`DATA-SCHEMA-CONTRACT`, `compiler/docs/data-model.md`
  * section 4): [[FhirShape]] rendered as Spark types. FHIR primitives are their JSON scalar types — temporal
  * and code values as strings, which the engine converts at use; integers as long and decimals as double,
  * cast to `decimal` at use. It is DISTINCT from the CQL canonical `Encoding` of a converted value, and it
  * is the explicit read schema that makes the retrieve frame lazy (`TYPE-OFFLINE-COMPILE`).
  *
  * Which fields exist is [[FhirShape]]'s rule (`DATA-MODEL-SCHEMA`, `DATA-MODEL-CUTS`): FHIR R4 JSON with
  * some fields left out, never moved or renamed, and no depth cap. */
object FhirSchema {

  /** The FHIR JSON-native Spark type of a primitive base. */
  private def primitiveSpark(base: String): DataType = base match {
    case "boolean"                                  => BooleanType
    case "integer" | "positiveInt" | "unsignedInt" => LongType
    case "decimal"                                  => DoubleType
    case _                                          => StringType // date/dateTime/instant/time/code/string/uri/id/…
  }

  private def spark(shape: Shape): DataType = shape match {
    case Leaf(base)     => primitiveSpark(base)
    case Struct(fields) => StructType(fields.map { f =>
      val t = spark(f.shape)
      StructField(f.name, if (f.isList) ArrayType(t) else t, nullable = true)
    })
  }

  private val cache = new ConcurrentHashMap[String, StructType]()

  /** The raw physical schema of a FHIR resource as it appears in NDJSON: the `resourceType` discriminator
    * plus every element [[FhirShape.resource]] keeps. The explicit read schema (`SparkFhirData`) and the
    * source of the `__resource` struct, alias and context schemas. */
  def rawResourceSchema(resourceType: String): StructType =
    cache.computeIfAbsent(FhirModel.localName(resourceType),
      rt => spark(FhirShape.resource(rt)).asInstanceOf[StructType])

  /** True iff `resourceType` is modeled (so an explicit-schema read is available). */
  def isModeled(resourceType: String): Boolean = FhirModel.isModeled(resourceType)
}
