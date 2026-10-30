# Coding standards

Read before writing code. These follow the existing code.

## General

- One implementation path per feature. No fallbacks or "try X, then Y" branches unless asked for. Let
  failures show.
- Fail loud (`CORE-NO-SILENT-MISLOWER`). Raise a typed error with useful text. Never swallow an error.
- No speculative abstractions. Three similar lines are better than an early helper.
- Don't regex-patch or string-patch output from an upstream dependency. Fix it upstream. A documented
  `CAST` or JSON round-trip at the point of use is fine as a stopgap tied to a tracked upstream fix.
- Derived data comes from the compiler or the backend. The frontend never computes from regex or source
  scans what the compiler provides or could provide. If it doesn't provide it yet, raise the gap.
- Don't build on code marked `TODO` or `HACK`. Replace it or raise it.
- Propose the approach before building new requirements, architecture or data-model changes, new
  dependencies or new derived data.

## Requirement citations

- Cite the requirement ID in the docstring of code that implements it, and in tests (`PROC-CITE-REQ`):
  `"""RUN-CLIENT-DURABLE: ..."""`.
- IDs are defined in [`requirements.md`](requirements.md) and in each feature's
  `<feature>/docs/requirements.md`.
- For CQL semantics, cite the HL7 CQL spec clause in a comment.

## Python

- `from __future__ import annotations` in every file.
- Imports: standard library, third-party, local, each group sorted.
- Import heavy dependencies (Databricks SDK, pyspark) inside the function that needs them.
- Types: `str | None`, `list[str]`, `dict[str, Any]`. Never `Optional`, `List` or `Dict`.
- Dataclasses for models. `Protocol` for store and service contracts, satisfied structurally.
- `snake_case` for functions and fields, `PascalCase` for classes, `_prefix` for private names.
- JSON on the wire is camelCase, through `to_dict()` and `from_dict()`. Omit `None` fields.
- Bare `except Exception:` only for best-effort cleanup, and log it. Re-raise with `from e`.
- Config resolves: specific env var, then fallback env var, then default.
- Don't add a `spark.cqldatabricks.*` conf key for a client setting. Serverless refuses to set it. Give the
  setting a `connect()` keyword and a `CQL_DATABRICKS_*` env var (`BUILD-RESOLVE`).
- Call `load_local_env()` (`cql_databricks_common.local_env`, `SVC-LOCAL-CONFIG-BUNDLE`) once, in
  `__main__.py`. Never call `load_dotenv()` directly.
- One logger per module: `log = logging.getLogger(__name__)`. INFO for state changes, DEBUG for
  frequent operations.
- Tests: pytest. Prefer real objects to mocks. Mock external services only when you must. Name tests for
  the scenario: `test_skip_fresh_library()`.

## TypeScript

- Function components with hooks. No class components, no Redux or Zustand.
- `useCallback` for handlers passed as props. `useMemo` only for expensive work.
- Wrap `localStorage` access in try/catch.
- `interface` for props, `type` for unions and aliases.
- PascalCase for component files (`AgentPane.tsx`), kebab-case for others (`run-client.ts`).
- Explicit `.ts` and `.tsx` extensions in imports.
- Plain CSS with design tokens as custom properties (`--bg-app`, `--accent`). No CSS-in-JS or CSS
  modules. Inline `style=` only for dynamic values.
- Semantic HTML with `aria-` attributes.
- `wire.ts` mirrors the backend's Pydantic models by hand.
- Tests: vitest and Testing Library, no snapshot tests. Query by role, text or label. Cite the
  requirement in the `describe` name.

## Scala

- The build runs with `-Xfatal-warnings`. Matches over the IR are exhaustive.
- `compiler/core` and `compiler/runtime/udf` take no Databricks dependency
  (`./gradlew checkNeutrality`).
- All SQL text is assembled in `SqlRender`, which quotes identifiers.

## Package layout

- Keep packages shallow. No circular imports. Dependencies point from high level to low level.
- A module that only one feature uses lives in that feature, not in `common/`.

## Database DDL

No feature uses Lakebase today. If one does again, its DDL lives only in the deploy definition, and seed
scripts issue DML only (`DEPLOY-LAKEBASE-SCHEMA`).
