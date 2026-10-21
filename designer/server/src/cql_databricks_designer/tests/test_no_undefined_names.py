"""No module may reference a name it never defined or imported.

**The defect class this suite is otherwise structurally blind to.** A Python function body is not
resolved until it runs, so a handler referencing an undefined name imports cleanly, registers cleanly,
and raises `NameError` only when a request reaches it. No test in this repository calls the route
handlers, and `test_app_routes.py` proves REGISTRATION rather than execution — so the path from "the
module imports" to "the endpoint answers" had no gate on it at all.

Routerizing `app.py` produced four such defects in a single change, each a live 500 on a real request:
`_blank_to_none` stayed behind while its only callers moved; `_translate_closure` ended up with callers
in two different modules; `KnowledgeBindingDto` was referenced by a model elsewhere; and `Any` was
unimported in seven modules — invisible because `from __future__ import annotations` makes annotations
strings, so nothing failed until something introspected a signature.

**Why `pyflakes` rather than a linter.** It answers one question — is every name resolvable — and brings
no opinion about line length, import order or quoting. A style tool here would mean either a large
mechanical diff or a long suppression list, and a suppression list is how a gate stops being read.

**Why this does not skip when `pyflakes` is absent.** A test that skips itself for a missing dependency
reports success over exactly the code it exists to check. That is the silent-no-op failure this
repository has now hit three times: the Specification-schema gate skipping for want of `xmlschema`, the
conformance `--rerun` flag reporting BUILD SUCCESSFUL having executed nothing, and `venv_py` built from
an undefined shell variable making every Python suite SKIP. `pyflakes` is declared in the `[dev]` extra
and its absence is a failure here, not an excuse.

**Unused imports are deliberately NOT gated.** There are 28 of them across the package, all predating
this gate (one fewer now: the repaired `orchestrator.py` shed the `parse_result_name` its dead helper
had wrapped). Failing on them would mean either a large unrelated cleanup inside an unrelated change or a
suppression list covering the same ground as the check — and neither buys what this file is for. Unused
imports do not break a request; undefined names do.
"""

from __future__ import annotations

import pathlib

import pytest

#: Every package the App runs, not just the one this test happens to live in.
#:
#: **Anchored on each `src/` root, deliberately.** Anchoring on the enclosing package would make this gate
#: shrink every time a package moves out of it: the modules would leave the scan silently at the exact
#: moment they were being edited. That is the same blindness the route-surface gate hit when
#: `include_router` started hiding routes behind a wrapper — a gate whose reach is defined by where it
#: sits rather than by what it is checking. RENAME-DESIGNER moved the Designer's server out of the
#: evaluator's distribution, and COMMON-LIBRARY moved the shared server code to `common/`, so the gate names each
#: root the Designer's App runs (the evaluator's client included). It lives with the designer because the designer
#: sits above the evaluator (`docs/systems.md`); it reads the evaluator's source, never imports it.
def _repo_root() -> pathlib.Path:
    for candidate in pathlib.Path(__file__).resolve().parents:
        if (candidate / "settings.gradle").is_file():
            return candidate
    raise RuntimeError("repository root not found")


ROOT = _repo_root()
SRC_ROOTS = (ROOT / "common" / "src", ROOT / "designer" / "server" / "src", ROOT / "evaluator" / "client" / "src")

#: The undefined names that predate this gate, each one still true and each one itemized so it can be
#: argued with. **This is a RATCHET, asserted EXACTLY**: a new undefined name fails the test, and fixing
#: one on this list ALSO fails it, with a message telling you to delete the row. A list that may only
#: shrink is a different thing from a suppression list that quietly grows.
#:
#: Empty. The ratchet is in place to catch any new undefined names that would slip through.
KNOWN_UNDEFINED: set[tuple[str, str]] = set()


def _undefined() -> set[tuple[str, str]]:
    """Every (module, name) pyflakes cannot resolve, as a set of distinct pairs."""
    try:
        import pyflakes.api
        import pyflakes.messages
        import pyflakes.reporter
    except ImportError:  # pragma: no cover — the message IS the behaviour
        pytest.fail("pyflakes is missing. It is declared in the [dev] extra and this gate needs it: "
                    "pip install -e './designer/server[dev]'")

    interesting = (pyflakes.messages.UndefinedName, pyflakes.messages.UndefinedLocal,
                   pyflakes.messages.UndefinedExport)
    found: set[tuple[str, str]] = set()
    syntax: list[str] = []

    class Collect(pyflakes.reporter.Reporter):
        def __init__(self) -> None:
            pass

        def unexpectedError(self, filename, msg):
            syntax.append(f"{filename}: {msg}")

        def syntaxError(self, filename, msg, lineno, offset, text):
            syntax.append(f"{filename}:{lineno}: {msg}")

        def flake(self, message):
            if isinstance(message, interesting):
                rel = pathlib.Path(message.filename).relative_to(ROOT).as_posix()
                found.add((rel, message.message_args[0]))

    reporter = Collect()
    for path in sorted(p for src in SRC_ROOTS for p in src.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        pyflakes.api.checkPath(str(path), reporter)
    assert not syntax, "modules that do not parse:\n  " + "\n  ".join(syntax)
    return found


def test_no_new_undefined_names():
    """Every name resolves, except the itemized backlog — and that list may only shrink."""
    found = _undefined()
    new = sorted(found - KNOWN_UNDEFINED)
    assert not new, (
        "modules reference names they never define or import:\n  "
        + "\n  ".join(f"{m}: {n}" for m, n in new)
        + "\n\nA name used in a function BODY is a NameError when that function runs. A name used only "
          "in an annotation is invisible at runtime but wrong for any type checker. Neither is a "
          "candidate for KNOWN_UNDEFINED, which is closed to new entries by design.")


def test_the_known_backlog_is_still_exactly_the_backlog():
    """A fixed entry must leave the list.

    Without this the ratchet is just a suppression list: an entry whose defect was repaired years ago
    reads as a live problem, and the next person cannot tell which rows still mean anything.
    """
    found = _undefined()
    fixed = sorted(KNOWN_UNDEFINED - found)
    assert not fixed, (
        "these are fixed — delete them from KNOWN_UNDEFINED:\n  "
        + "\n  ".join(f"{m}: {n}" for m, n in fixed))
