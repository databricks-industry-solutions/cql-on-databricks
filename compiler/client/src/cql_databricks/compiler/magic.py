"""The ``%%cql`` cell magic — CQL as a first-class notebook cell language (`SVC-NOTEBOOK-CQL-MAGIC`).

Load it with ``%load_ext cql_databricks.compiler``, then a cell whose first line is ``%%cql`` IS CQL::

    %%cql
    library Diabetes version '1.0.0'
    using FHIR version '4.0.1'
    valueset "Diabetes": 'urn:cql_databricks:vs:diabetes'
    context Patient
    define "Numerator": exists ([Condition: "Diabetes"])

**A cell declares; it never runs.** Running the cell is like running a Python ``def``: the defines are now
available, nothing is evaluated, and closing the notebook costs nothing more. The cell IS compiled, so a CQL
error fails in the cell that caused it, and a cell that fails changes nothing. A later Python cell runs what
the cells declared, through the same `SVC-ZERO-CONFIG-NOTEBOOK` path as any other ``cql()`` call::

    cql(library="Diabetes").exec("Numerator")

A cell with a `library` line is its own library (`SVC-NOTEBOOK-CELL-LIBRARY`): a later cell ``include``s it
and Python reaches it as ``cql(library="Diabetes")``. A cell with no `library` line joins the session library
(`SVC-NOTEBOOK-SESSION-SCOPE`): a later such cell sees its defines unqualified, redeclaring a name replaces it,
and Python reaches it as ``cql()``.

The magic takes no options. Choosing defines and binding a result are Python's: ``q = cql(...)``,
``q.exec("A", "B")``.
"""
from __future__ import annotations

from IPython.core.error import UsageError
from IPython.core.magic import Magics, cell_magic, magics_class

from .libraries import identity_of
from .zeroconfig import cql as _bind_cql
from .session_libraries import register_cell


@magics_class
class CqlMagics(Magics):
    """Holds ``%%cql``. The IPython shell is the source of the Spark session, so the magic carries no state of
    its own."""

    @cell_magic
    def cql(self, line, cell):
        """Compile the cell to check it, then declare it in this notebook's Spark session; run nothing
        (`SVC-NOTEBOOK-CQL-MAGIC`)."""
        if line.strip():
            raise UsageError(
                f"%%cql takes no options, and got {line.strip()!r}: a cell only declares. Run its defines from "
                "a Python cell: cql(library=\"<Name>\").exec(\"<define>\") for a cell with a `library` line, "
                "cql().exec(\"<define>\") for one without (SVC-NOTEBOOK-CQL-MAGIC).")
        spark = self.shell.user_ns.get("spark")
        name, _ = identity_of(cell)
        if name is None:
            # SVC-NOTEBOOK-SESSION-SCOPE: binding header-less CQL joins the session library, and it joins only
            # if the merged library compiles.
            _bind_cql(cell, spark=spark)
            print("added to the session library — run its defines from Python with cql().exec(\"<define>\")")
            return
        # SVC-NOTEBOOK-CELL-LIBRARY: compile first and register after, so a cell that fails leaves the
        # library it would have replaced in place.
        _bind_cql(cell, spark=spark).describe()
        declared = register_cell(cell, spark=spark)
        print(f"declared {declared.ref} — include it, or run it from Python with cql(library=\"{name}\")")


def load_ipython_extension(ipython) -> None:
    """``%load_ext cql_databricks.compiler`` — register :class:`CqlMagics` on ``ipython``."""
    ipython.register_magics(CqlMagics)
