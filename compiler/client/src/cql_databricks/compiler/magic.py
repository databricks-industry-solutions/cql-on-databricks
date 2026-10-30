"""The ``%%cql`` cell magic — CQL as a first-class notebook cell language (`SVC-NOTEBOOK-CQL-MAGIC`).

Load it with ``%load_ext cql_databricks.compiler``, then a cell whose first line is ``%%cql`` IS a CQL library::

    %%cql --df gaps --target Numerator
    library Diabetes version '1.0.0'
    using FHIR version '4.0.1'
    valueset "Diabetes": 'urn:cql_databricks:vs:diabetes'
    context Patient
    define "Numerator": exists ([Condition: "Diabetes"])

The cell body is sent verbatim to the deployed Compiler Service and the resulting Spark SQL runs on the
notebook's own session — the same `SVC-ZERO-CONFIG-NOTEBOOK` path as :func:`cql_databricks.compiler.cql`, so the
magic adds no second execution route, only a nicer front door. ``--df`` binds the resulting DataFrame into
the user namespace so the NEXT (plain Python or SQL) cell picks the result up: that hand-off is the point,
and it is what makes CQL compose with the rest of a notebook rather than being a dead end.

Which defines run: ``--target`` if given (repeatable), else the measure's auto-detected eCQM populations,
else every define (`BUILD-CLIENT-POPULATIONS`). One target binds a DataFrame; several bind a
``{define: DataFrame}`` dict.

A cell with NO `library` header joins the session library (`SVC-NOTEBOOK-SESSION-SCOPE`), as a SQL cell's
view lands in the session's namespace: a later cell sees its defines unqualified, and redeclaring a name
replaces it. It runs only its own defines. A cell of declarations alone (a `valueset`, a `parameter`) runs
nothing; it is there for later cells.

A cell that declares a `library` header is its own library, REGISTERED as a session library
(`SVC-NOTEBOOK-CELL-LIBRARY`), so a later cell can ``include`` it and a measure can be written across
cells. ``--library`` registers without running, for a cell of shared definitions there is no point
evaluating.
"""
from __future__ import annotations

from IPython.core.error import UsageError
from IPython.core.magic import Magics, cell_magic, magics_class
from IPython.core.magic_arguments import argument, magic_arguments, parse_argstring

from .zeroconfig import cql as _run_cql
from .session_libraries import register_cell


@magics_class
class CqlMagics(Magics):
    """Holds ``%%cql``. The IPython shell is the source of both the Spark session and the namespace the
    result is bound into, so the magic carries no state of its own."""

    @magic_arguments()
    @argument("--df", metavar="NAME",
              help="bind the result into this variable (a DataFrame, or {define: DataFrame} for several "
                   "targets) instead of only displaying it")
    @argument("--target", action="append", metavar="DEFINE",
              help="the define to evaluate; repeat for several. Default: the measure's populations, "
                   "else every define")
    @argument("--library", action="store_true",
              help="declare only: register this cell as a session library for later cells to include, "
                   "and do not compile or run it")
    @cell_magic
    def cql(self, line, cell):
        """Compile the cell as a CQL library, run it on this notebook's Spark session, and display the
        result (`SVC-NOTEBOOK-CQL-MAGIC`).

        The cell is registered as a session library first (`SVC-NOTEBOOK-CELL-LIBRARY`) so a later cell
        can `include` it; `--library` stops there, which is what a cell of shared definitions wants — no
        compile, no round trip, nothing to evaluate."""
        args = parse_argstring(self.cql, line)
        # CLIENT-SESSION-STATE: the cell belongs to this notebook's Spark session, beside its mount.
        declared = register_cell(cell, spark=self.shell.user_ns.get("spark"))

        if args.library:
            if declared is None:
                raise UsageError(
                    "--library needs a `library <Name> version '<v>'` header — that identity is what a "
                    "later cell's `include` resolves. Drop --library: a cell with no header joins the "
                    "session library (SVC-NOTEBOOK-SESSION-SCOPE).")
            print(f"declared {declared.ref} — includable by later cells")
            return

        query = _run_cql(cell, spark=self.shell.user_ns.get("spark"))

        targets = args.target or query.default_targets()
        if not targets:
            if args.df:
                raise UsageError("--df needs a define to bind, and this cell declares none")
            print("added to the session library — no defines to run")
            return
        frames = query.exec(*targets) if len(targets) > 1 else {targets[0]: query.exec(targets[0])}

        for name, df in frames.items():
            if len(frames) > 1:
                print(f"--- {name} ---")
            self._display(df)

        if args.df:
            self.shell.user_ns[args.df] = frames[targets[0]] if len(frames) == 1 else frames

    def _display(self, df) -> None:
        """Render a result frame the way the surrounding notebook would. Databricks injects a rich
        ``display`` into the notebook namespace; plain IPython has none, so fall back to Spark's printer."""
        renderer = self.shell.user_ns.get("display")
        if renderer is None:
            df.show()
        else:
            renderer(df)


def load_ipython_extension(ipython) -> None:
    """``%load_ext cql_databricks.compiler`` — register :class:`CqlMagics` on ``ipython``."""
    ipython.register_magics(CqlMagics)
