package com.databricks.cql.it

import com.databricks.cql.retrieve.{DataProvider, RetrieveHints}
import org.apache.spark.sql.types.DataType
import org.apache.spark.sql.{DataFrame, functions => F}

/** BIND-CONTRACT: a harness data source that returns a type's resources the way a data view does, the `resource`
  * struct, and optionally `patient_id`, `min_date` and `max_date` (DATA-SUBJECT-KEY, BIND-PREFILTER). The
  * differential binds these frames as its data views. */
trait FhirColumns {
  def fhirColumns(fhirType: String, hints: RetrieveHints = RetrieveHints.none): DataFrame
}

/** DATA-MODEL-SUBSET, DATA-SUBJECT-KEY, BIND-PREFILTER: the sample data bound as an implementer's view that returns
  * a subset of FHIR R4. For each type in `shapes`, the view's `resource` keeps only the fields that shape names
  * (the Spark type text of the struct), at every depth; other types are returned whole. For each type in `access`,
  * the view also returns the root columns given (`patient_id`, `min_date`, `max_date`), each the SQL expression
  * given, over `resource`. The reference engine reads the full NDJSON, so a fixture over it agrees with the reference only
  * when what the shape leaves out holds no data, each patient_id is the id the other views' references name, and
  * the dates cover their rows: proof that the compiler reads only what the view returns, keys on the patient_id it
  * is given, and that the coarse filter drops no row the refine step keeps. */
final class NarrowedFhirData(inner: SparkFhirData, shapes: Map[String, String],
                             access: Map[String, Seq[(String, String)]] = Map.empty)
  extends DataProvider with FhirColumns {

  override def retrieve(fhirType: String, hints: RetrieveHints = RetrieveHints.none): DataFrame =
    inner.retrieve(fhirType, hints)

  override def fhirColumns(fhirType: String, hints: RetrieveHints = RetrieveHints.none): DataFrame = {
    val frame = inner.fhirColumns(fhirType, hints)
    val resource = com.databricks.cql.sema.ResourceShape.ResourceColumn
    val narrowed = shapes.get(fhirType) match {
      case None => frame
      case Some(shape) =>
        val keep = DataType.fromDDL(shape)
        frame.select(F.from_json(F.to_json(F.col(resource)), keep, Map.empty[String, String]).as(resource))
    }
    access.getOrElse(fhirType, Nil).foldLeft(narrowed) { case (df, (column, sql)) => df.withColumn(column, F.expr(sql)) }
  }
}
