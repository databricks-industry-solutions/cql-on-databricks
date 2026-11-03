"""BIND-RESHAPE — one reshape from FHIR data to the data contract, ``(patient_id, resource)``.

Every path that turns FHIR data into the contract (`BIND-CONTRACT`) uses this module: the view behind a volume or
DataFrame relation, the data-source publish step, and the loader and deploy, which write the store in the same form.
It generates SQL over a named relation, so the same text is the body of a published per-type view (`BIND-PUBLISH`),
the query behind a session view and the store's rows.

Two physical encodings:

- **JSON**: one resource per row as JSON text. Parsed with ``from_json`` against the model's struct, so
  missing fields read as null and extra fields are dropped. Parsing runs on every read.
- **Structured**: the resource's fields as columns, or a relation already on the contract (a ``resource``
  column). The model's struct is rebuilt field by field: a missing field is a typed null, an extra field is
  dropped, and a safe widening (``int`` to ``long``) is cast. Anything else is an error, found before any SQL
  runs (`BIND-SCHEMA-CHECK`).

``resource`` holds FHIR's fields only, with no engine lineage (`DATA-EXPORT`). ``patient_id`` (`DATA-SUBJECT-KEY`)
is the source's own ``patient_id`` when it has one; else ``id`` for Patient, the trailing id of ``subject``,
``patient`` or ``beneficiary`` ``.reference`` for a Patient-compartment type (``Patient/<id>``, ``urn:uuid:<id>``
and full URLs), and null otherwise. Every identifier is quoted, and no SQL text is taken from a caller.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

#: DATA-SUBJECT-KEY — the reference fields a Patient-compartment resource names its patient in, in order.
SUBJECT_REF_FIELDS = ("subject", "patient", "beneficiary")

#: BIND-CONTRACT — the root column a data view returns the resource in, a struct of FHIR's fields.
RESOURCE = "resource"

#: DATA-SUBJECT-KEY — the root column a data view returns its subject key in, beside ``resource``.
PATIENT_ID = "patient_id"

ERROR, WARN, INFO = "ERROR", "WARN", "INFO"

_TABLE_NAME = re.compile(r"^[^./`\s]+\.[^./`\s]+\.[^./`\s]+$")


@dataclass(frozen=True, kw_only=True)
class SchemaIssue:
    """One finding about a source against the model (`BIND-SCHEMA-CHECK`). ``path`` is the field, dotted
    (``code.coding.system``), or empty for the whole source. An ``ERROR`` refuses the source."""
    severity: str
    fhir_type: str
    path: str
    message: str

    def __str__(self) -> str:
        where = f"{self.fhir_type}.{self.path}" if self.path else self.fhir_type
        return f"{self.severity} {where}: {self.message}"


class SchemaCheckError(ValueError):
    """A source refused by the check, listing every error at once (`BIND-SCHEMA-CHECK`)."""

    def __init__(self, issues: Sequence[SchemaIssue]):
        self.issues = list(issues)
        errors = [i for i in self.issues if i.severity == ERROR]
        super().__init__(f"{len(errors)} schema error(s) against the FHIR model:\n"
                         + "\n".join(f"  {i}" for i in errors))


def table_name(value: str) -> str:
    """``value`` as a table name, which must be ``catalog.schema.table``. A string is never guessed at."""
    if not isinstance(value, str) or not _TABLE_NAME.match(value):
        raise ValueError(f"{value!r} is not a table name: write it as catalog.schema.table")
    return value


def quote_name(name: str) -> str:
    """A dotted relation name, each part quoted."""
    return ".".join(_ident(part) for part in name.split("."))


def json_sql(relation: str, schema, *, column: str, type_filter: Optional[str] = None) -> str:
    """The reshape of JSON text in ``column`` of ``relation`` (quoted SQL) to the contract for ``schema``, a
    :class:`~.providers.ResourceSchema` (:func:`_select`). ``type_filter``
    keeps only rows whose resource is that type, read from ``type_filter``'s column, or from ``$.resourceType`` when
    it names none (see :func:`type_predicate`). Blank rows are skipped."""
    col = _ident(column)
    fields = [(f.name, f"`__r`.{_ident(f.name)}") for f in schema.struct.fields]
    where = f"{col} IS NOT NULL AND length(trim({col})) > 0" + (f" AND {type_filter}" if type_filter else "")
    return (f"SELECT {_select(_subject_from_json(schema, col), fields, nullable='`__r`')} "
            f"FROM (SELECT {col}, from_json({col}, {_literal(schema.ddl)}) AS `__r` FROM {relation} "
            f"WHERE {where})")


def volume_sql(path: str, schema, *, layout: str) -> str:
    """BIND-RELATION: the reshape of ``schema``'s type in a volume folder, read with ``read_files``: its
    ``<Type>.ndjson`` file (``layout="ndjson"``), or the entries of every FHIR Bundle in it (``"bundles"``),
    whose resources are kept when their ``resourceType`` is the type."""
    folder = path.rstrip("/")
    if layout == "ndjson":
        source = f"read_files({_literal(f'{folder}/{schema.fhir_type}.ndjson')}, format => 'text')"
        return json_sql(source, schema, column="value")
    entries = (f"(SELECT `__e`.`resource` AS `value` FROM (SELECT explode(from_json(`value`, "
               f"'struct<entry: array<struct<resource: string>>>').`entry`) AS `__e` "
               f"FROM read_files({_literal(folder)}, format => 'text', wholeText => true)))")
    return json_sql(entries, schema, column="value",
                    type_filter=type_predicate(schema.fhir_type, json_column="value", type_column=None))


def type_predicate(fhir_type: str, *, json_column: str, type_column: Optional[str]) -> str:
    """The predicate that keeps rows of ``fhir_type`` in a mixed-type JSON relation."""
    if type_column is not None:
        return f"{_ident(type_column)} = {_literal(fhir_type)}"
    return f"get_json_object({_ident(json_column)}, '$.resourceType') = {_literal(fhir_type)}"


def structured_sql(relation: str, source_schema, schema) -> Tuple[str, List[SchemaIssue]]:
    """The reshape of a structured ``relation`` (quoted SQL) whose Spark schema is ``source_schema`` to the contract
    for ``schema`` (:func:`_select`), and the issues found on the way. With any ``ERROR`` the SQL is not usable."""
    names = set(source_schema.fieldNames())
    issues: List[SchemaIssue] = []
    on_contract = RESOURCE in names
    if on_contract:
        resource, select = source_schema[RESOURCE].dataType, f"{_ident(RESOURCE)} AS `__r`"
    else:
        resource, select = source_schema, "struct(*) AS `__r`"
    # A relation that carries its own patient_id keeps it: the key is the publisher's to define.
    subject_given = PATIENT_ID in names
    if subject_given:
        select += f", {_ident(PATIENT_ID)}"
    if _struct(resource) is None:
        return "", [SchemaIssue(severity=ERROR, fhir_type=schema.fhir_type, path=RESOURCE,
                                message=f"is {resource.simpleString()}, not a struct")]
    fields = _conform_fields("`__r`", resource, schema.struct, schema.fhir_type, "", issues, depth=0, top=True)
    subject = _ident(PATIENT_ID) if subject_given else _subject_from_struct(schema, resource, issues)
    projection = _select(subject, fields, nullable="`__r`" if on_contract else None)
    return f"SELECT {projection} FROM (SELECT {select} FROM {relation})", issues


def _select(subject: str, fields: Sequence[Tuple[str, str]], nullable: Optional[str]) -> str:
    """The projection of one reshape: the data contract (`BIND-CONTRACT`), the subject key as ``patient_id``
    (`DATA-SUBJECT-KEY`) and the ``resource`` struct of FHIR's fields. ``patient_id`` comes first, so a table written
    from it has file statistics on the key: Delta collects them for the first 32 columns, counting each struct field.
    ``nullable`` is the parsed struct whose null makes the resource null."""
    entries = ", ".join(f"{_literal(name)}, {value}" for name, value in fields)
    resource = f"named_struct({entries})"
    if nullable is not None:
        resource = f"CASE WHEN {nullable} IS NULL THEN NULL ELSE {resource} END"
    return f"{subject} AS {_ident(PATIENT_ID)}, {resource} AS {_ident(RESOURCE)}"


def view_shape(schema) -> str:
    """DATA-MODEL-SUBSET: the shape of a view over the reshape of ``schema``'s type, its row as Spark type text."""
    return f"struct<{_ident(PATIENT_ID)}:string,{_ident(RESOURCE)}:{schema.struct.simpleString()}>"


def require_ok(issues: Sequence[SchemaIssue]) -> None:
    """Raise :class:`SchemaCheckError` when any issue is an error."""
    if any(i.severity == ERROR for i in issues):
        raise SchemaCheckError(issues)


class FhirJson:
    """Raw FHIR JSON, one resource per row, of mixed types (`BIND-RESHAPE`).

    ``relation`` is a table name (``catalog.schema.table``) or a DataFrame. ``column`` holds the JSON. Each
    row's type is ``type_column``'s value, else the JSON's ``resourceType``. ``types`` names the types it
    supplies; ``None`` means every modeled type found in the data.

    Reading it parses JSON on every query. To read it fast, write :meth:`to_df` to a table."""

    def __init__(self, relation, *, column: str, type_column: Optional[str] = None,
                 types: Optional[Sequence[str]] = None) -> None:
        self.relation = table_name(relation) if isinstance(relation, str) else relation
        self.column = column
        self.type_column = type_column
        self.types = None if types is None else sorted(set(types))

    @property
    def is_table(self) -> bool:
        return isinstance(self.relation, str)

    def to_df(self, spark, fhir_type: str, *, client=None):
        """The ``fhir_type`` rows on the data contract, as a DataFrame. ``client`` supplies the model's
        schema (`CLIENT-RESOURCE-SCHEMA`); it defaults to the zero-config client."""
        schema = model_client(spark, client).resource_schema(fhir_type)
        return self.query(spark, schema)

    def query(self, spark, schema):
        """The reshape for ``schema``'s type as a DataFrame over this relation."""
        predicate = type_predicate(schema.fhir_type, json_column=self.column, type_column=self.type_column)
        if self.is_table:
            return spark.sql(json_sql(quote_name(self.relation), schema, column=self.column, type_filter=predicate))
        return json_frame(spark, self.relation, schema, column=self.column, type_filter=predicate)

    def found_types(self, spark) -> List[str]:
        """The resource types in the data, as written: ``types`` when given, else read from the rows."""
        if self.types is not None:
            return list(self.types)
        expr = (_ident(self.type_column) if self.type_column is not None
                else f"get_json_object({_ident(self.column)}, '$.resourceType')")
        source = quote_name(self.relation) if self.is_table else "{source}"
        sql = f"SELECT DISTINCT {expr} AS `t` FROM {source} WHERE {expr} IS NOT NULL"
        rows = spark.sql(sql).collect() if self.is_table else spark.sql(sql, source=self.relation).collect()
        return sorted(r["t"] for r in rows)

    def __repr__(self) -> str:
        what = self.relation if self.is_table else "<DataFrame>"
        return f"FhirJson({what}, column={self.column!r})"


def json_frame(spark, frame, schema, *, column: str, type_filter: Optional[str] = None):
    """:func:`json_sql` over a DataFrame, as a DataFrame. The loader reshapes its files through this."""
    sql = json_sql("{source}", _braces(schema), column=column, type_filter=type_filter)
    return spark.sql(sql, source=frame)


def schema_from_ddl(fhir_type: str, ddl: str, *, patient_context: bool):
    """A :class:`~.providers.ResourceSchema` from the model's DDL, for a caller that holds the DDL already
    (the loader's vendored copy). Needs an active Spark session to parse it."""
    from pyspark.sql.types import StructType  # lazy

    from .providers import ResourceSchema

    return ResourceSchema(fhir_type=fhir_type, struct=StructType.fromDDL(ddl), ddl=ddl,
                          patient_context=patient_context)


def model_client(spark, client):
    """``client``, else the zero-config client on ``spark``: the source of the model's schema."""
    if client is not None:
        return client
    from .zeroconfig import connect  # lazy: the Databricks layer

    return connect(spark)


def _braces(schema):
    """``schema`` with its DDL safe inside ``spark.sql``'s ``{name}`` formatting."""
    from dataclasses import replace

    return replace(schema, ddl=schema.ddl.replace("{", "{{").replace("}", "}}"))


# -- the struct walk ---------------------------------------------------------------------------------

def _conform(expr: str, source, target, fhir_type: str, path: str, issues: List[SchemaIssue],
             depth: int) -> str:
    """``expr`` (of Spark type ``source``) as the model's ``target`` type, recording what it took."""
    if _same(source, target):
        return expr
    if _struct(target) is not None and _struct(source) is not None:
        return _conform_struct(expr, source, target, fhir_type, path, issues, depth)
    if _array(target) is not None and _array(source) is not None:
        var = f"`__e{depth}`"
        inner = _conform(var, source.elementType, target.elementType, fhir_type, path, issues, depth + 1)
        return expr if inner == var else f"transform({expr}, {var} -> {inner})"
    if _widens(source, target):
        issues.append(SchemaIssue(severity=INFO, fhir_type=fhir_type, path=path,
                                  message=f"{source.simpleString()} is cast to {target.simpleString()}"))
        return f"CAST({expr} AS {sql_type(target)})"
    if _string(source) and (_struct(target) is not None or _array(target) is not None):
        issues.append(SchemaIssue(severity=ERROR, fhir_type=fhir_type, path=path,
                                  message=f"is a JSON string; the model has {target.simpleString()}. Parse it, "
                                          f"or publish the raw JSON as FhirJson"))
        return "NULL"
    issues.append(SchemaIssue(severity=ERROR, fhir_type=fhir_type, path=path,
                              message=f"is {source.simpleString()}; the model has {target.simpleString()}, and "
                                      f"no cast is safe"))
    return "NULL"


def _conform_struct(expr: str, source, target, fhir_type: str, path: str, issues: List[SchemaIssue],
                    depth: int) -> str:
    fields = _conform_fields(expr, source, target, fhir_type, path, issues, depth)
    return (f"CASE WHEN {expr} IS NULL THEN NULL ELSE "
            f"named_struct({', '.join(f'{_literal(n)}, {v}' for n, v in fields)}) END")


def _conform_fields(expr: str, source, target, fhir_type: str, path: str, issues: List[SchemaIssue],
                    depth: int, top: bool = False) -> List[Tuple[str, str]]:
    """Each field of ``target`` as ``(name, value)``, read from ``expr`` of type ``source``."""
    present = {f.name: f for f in source.fields}
    wanted = {f.name for f in target.fields}
    parts = []
    for field in target.fields:
        sub = f"{path}.{field.name}" if path else field.name
        if field.name in present:
            parts.append((field.name, _conform(f"{expr}.{_ident(field.name)}", present[field.name].dataType,
                                               field.dataType, fhir_type, sub, issues, depth)))
        else:
            if not top or field.name != "id":
                issues.append(SchemaIssue(severity=WARN, fhir_type=fhir_type, path=sub,
                                          message="is missing and reads as null"))
            parts.append((field.name, f"CAST(NULL AS {sql_type(field.dataType)})"))
    for name in sorted(present.keys() - wanted):
        sub = f"{path}.{name}" if path else name
        slots = sorted(w for w in wanted if w.startswith(name) and w[len(name):][:1].isupper())
        if slots:
            issues.append(SchemaIssue(severity=ERROR, fhir_type=fhir_type, path=sub,
                                      message=f"is one merged choice field; the model splits it into "
                                              f"{', '.join(slots[:4])}{', …' if len(slots) > 4 else ''}"))
        elif not (top and name == PATIENT_ID):
            issues.append(SchemaIssue(severity=INFO, fhir_type=fhir_type, path=sub,
                                      message="is not in the model and is dropped"))
    if top and "id" not in present:
        issues.append(SchemaIssue(severity=ERROR, fhir_type=fhir_type, path="id",
                                  message="is missing; every resource needs its id"))
    return parts


# -- the subject key (DATA-SUBJECT-KEY) ----------------------------------------------------------------

def _subject_from_json(schema, col: str) -> str:
    if schema.fhir_type == "Patient":
        return f"get_json_object({col}, '$.id')"
    if not schema.patient_context:
        return "CAST(NULL AS STRING)"
    refs = ", ".join(f"get_json_object({col}, '$.{f}.reference')" for f in SUBJECT_REF_FIELDS)
    return _trailing_id(f"coalesce({refs})")


def _subject_from_struct(schema, resource, issues: List[SchemaIssue]) -> str:
    if schema.fhir_type == "Patient":
        return "CAST(`__r`.`id` AS STRING)"
    if not schema.patient_context:
        return "CAST(NULL AS STRING)"
    fields = {f.name: f.dataType for f in resource.fields}
    refs = [f"`__r`.{_ident(f)}.`reference`" for f in SUBJECT_REF_FIELDS
            if _struct(fields.get(f)) is not None and "reference" in fields[f].fieldNames()
            and _string(fields[f]["reference"].dataType)]
    if not refs:
        issues.append(SchemaIssue(severity=ERROR, fhir_type=schema.fhir_type, path="",
                                  message=f"has no {', '.join(f + '.reference' for f in SUBJECT_REF_FIELDS)} "
                                          f"to derive patient_id from"))
        return "CAST(NULL AS STRING)"
    return _trailing_id(refs[0] if len(refs) == 1 else f"coalesce({', '.join(refs)})")


def _trailing_id(reference: str) -> str:
    """The run after the last ``/`` or ``:`` of a reference; empty reads as null."""
    return f"nullif(regexp_extract({reference}, '([^/:]+)$', 1), '')"


# -- Spark types ---------------------------------------------------------------------------------------

def _type_name(data_type) -> str:
    return type(data_type).__name__


def _struct(data_type):
    return data_type if data_type is not None and _type_name(data_type) == "StructType" else None


def _array(data_type):
    return data_type if data_type is not None and _type_name(data_type) == "ArrayType" else None


def _string(data_type) -> bool:
    return data_type is not None and _type_name(data_type) in ("StringType", "VarcharType", "CharType")


def _same(a, b) -> bool:
    """Equal as Spark types, ignoring nullability and metadata."""
    return _shape(a) == _shape(b)


def _shape(data_type):
    if _struct(data_type) is not None:
        return ("struct", tuple((f.name, _shape(f.dataType)) for f in data_type.fields))
    if _array(data_type) is not None:
        return ("array", _shape(data_type.elementType))
    if _type_name(data_type) == "MapType":
        return ("map", _shape(data_type.keyType), _shape(data_type.valueType))
    return ("string",) if _string(data_type) else (data_type.simpleString(),)


_INTEGRAL = ("ByteType", "ShortType", "IntegerType", "LongType")


def _widens(source, target) -> bool:
    """A cast that loses nothing: a wider integer, a float to a double, an integer to a double, a date to
    its ISO string. A timestamp is not one: its string form is not a FHIR dateTime."""
    s, t = _type_name(source), _type_name(target)
    if s in _INTEGRAL and t in _INTEGRAL:
        return _INTEGRAL.index(s) < _INTEGRAL.index(t)
    if t == "DoubleType":
        return s in ("FloatType", "ByteType", "ShortType", "IntegerType")
    return s == "DateType" and _string(target)


# -- SQL text --------------------------------------------------------------------------------------------

def sql_type(data_type) -> str:
    """A Spark type as SQL type text, every field name quoted, so a FHIR field named like a keyword
    (``Period.end``) parses. Never truncated, unlike Spark's own ``simpleString`` on a wide struct."""
    if _struct(data_type) is not None:
        return "STRUCT<" + ", ".join(f"{_ident(f.name)}: {sql_type(f.dataType)}" for f in data_type.fields) + ">"
    if _array(data_type) is not None:
        return f"ARRAY<{sql_type(data_type.elementType)}>"
    if _type_name(data_type) == "MapType":
        return f"MAP<{sql_type(data_type.keyType)}, {sql_type(data_type.valueType)}>"
    return data_type.simpleString()


def _ident(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def _literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
