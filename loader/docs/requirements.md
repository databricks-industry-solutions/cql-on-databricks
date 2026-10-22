# Loader requirements

Requirements the loader owns. Requirements that span features, and the rule for where a requirement
lives, are in [`docs/requirements.md`](../../docs/requirements.md). Each ID is stable, unique across
every registry, and cited from code (`PROC-CITE-REQ`). A requirement that stops being true moves to
[Retired](#retired) with a one-line reason, so old citations still resolve.

## Content

Importing, storing and resolving eCQM content and value sets.

| ID | Requirement |
|---|---|
| `CONTENT-BAKE-AUTHORITY` | Content-ingest schemas baked in (one per content source; VSAC → vsac); not parameterized. Schema names are defaults. Additional schemas added as named constants. Amended 2026-10-21: the eCQI import's defaults are `ecqi_measures` and `vsac`; the samples land in `sample_measures` (`CONTENT-SAMPLE-PRELOAD`). |
| `CONTENT-CATALOG-LAYOUT` | Authority-segregated schemas: catalog.&lt;authority&gt;.{measure, library, valueset_ref} for measure authorities; catalog.vsac.{valueset} for VSAC. |
| `CONTENT-ECQI-LOAD-UNCHANGED` | An imported package's `Measure` and `Library` resources are loaded as shipped: no Measurement Period edit, no stripping, no relabelling. Added 2026-10-21. |
| `CONTENT-ECQI-PIN` | The repository kept a tracked list of eCQI packages to fetch. SUPERSEDED 2026-10-21 by `CONTENT-ECQI-USER-PACKAGE`: the repository keeps no list of measures; the user names the package. |
| `CONTENT-ECQI-TERMS-GATE` | Before any download, the eCQI import shows the third-party terms and requires typing `I AGREE`; after the download, it shows each Measure's own `copyright` field verbatim and requires `I AGREE` again before any content is loaded. Anything else cancels. A non-interactive run must pass `--accept-terms`, which still shows both texts; the acceptance is logged with the package, its SHA-256, the local user and the time. The terms text is a draft pending legal review. Added 2026-10-21. |
| `CONTENT-ECQI-USER-PACKAGE` | The user names the eCQI package to import, by its eCQI file name or a local zip. The repository holds no pinned list, no lock and no tracked measure list. Added 2026-10-21. |
| `CONTENT-FHIR-VARIANT` | Knowledge tables store each FHIR resource in one `VARIANT` column, so nothing is lost and no schema is generated. A typed struct is not used: the Measure resource alone has thousands of fields. |
| `CONTENT-FIXTURE-ONE-PER-SUBJECT` | Test-case identity is (measure directory, patient id); keep exactly ONE bundle per key. De-duplicate, do not restrict: all layout paths used. Bundle with no Patient ID is reported separately. SUPERSEDED 2026-10-21: the importer no longer loads test cases. |
| `CONTENT-IG-ENGINE-ALIGN` | Content-ingest and the engine read the same upstream implementation-guide repository. SUPERSEDED 2026-10-21: nothing is fetched from an implementation-guide repository. Value-set expansions come from VSAC with the user's own key (`CONTENT-VSAC`). |
| `CONTENT-IMPORT-TARGET-TAG` | Importer loads into named catalog.schema and tags it cql_databricks_knowledge / cql_databricks_terminology. --tag-only tags an earlier load. Tags need Unity Catalog. |
| `CONTENT-IMPORTER-FHIR-SCHEMAS` | Importer owns its FHIR resource schemas (content_ingest/resource-schemas.json). Compiler's dumpResourceSchemas no longer overwrites it; retrieve UDTF adapts, so they can drift without breaking runs. |
| `CONTENT-INCREMENTAL` | MERGE on (url, version) for Measure/Library (upsert). Delete-and-append by source_package for ValueSet references. Re-runnable without data loss. |
| `CONTENT-INGEST` | Standalone ETL tool fetches publicly available FHIR Measure/Library resources and loads into Unity Catalog; zero dependency on app or engine. One IG source and one content schema named for it. Amended 2026-10-21: two inputs, the sample content in `examples/` (`CONTENT-SAMPLE-PRELOAD`) and one eCQI package the user names behind its terms (`CONTENT-ECQI-TERMS-GATE`). There is no IG source. |
| `CONTENT-LAKEHOUSE-STANDARD-ONLY` | Published tables are standardized FHIR resources or do not belong. Junctions and denormalizations are derived downstream. Removes measure_library and valueset_ref from published schema. Valueset keeps tall shape (engine binding contract). |
| `CONTENT-MARKETPLACE-READY` | Table layout and provenance columns designed for eventual Databricks Marketplace publishing. |
| `CONTENT-MEASURE-LIBRARY-LINK` | The link from a Measure to its root CQL Library is derived from the Measure resource when read. It is an index with no truth of its own. |
| `CONTENT-MEASURE-NAME-PUBLISHED` | Corpus stores the published measure name read from the package header. Fixtures found by draft name (pre-publication), stored under published name. Pins stay the single place the rename is recorded. SUPERSEDED 2026-10-21: there are no fixtures and no pins, so there is no rename to record. |
| `CONTENT-NO-DERIVED-COLUMNS` | A knowledge table holds the resource, its identity and its provenance only. `url` and `version` are generated columns. Anything else derived from the resource is computed when read. |
| `CONTENT-PIN-ASSERT` | Fetch aborts unless (a) every CQL file header agrees with its filename, and (b) root library matches pin's name and version exactly. Catches version lies. Every problem reported, not the first. SUPERSEDED 2026-10-21 by `CONTENT-ECQI-USER-PACKAGE`: there is no pin to assert against. The package is loaded as shipped (`CONTENT-ECQI-LOAD-UNCHANGED`). |
| `CONTENT-PIN-IG-TEST-DIR` | MeasurePin records ig_test_dir (measure directory under IG repo). Required, not optional. IG archive names fixtures with pre-publication name; published names are renamed by hand; the pin records the rename. SUPERSEDED 2026-10-21: there are no pins. |
| `CONTENT-PIN-LOCAL-EDIT` | Fetched packages carried one documented local edit: binding the measurement-period parameter default. SUPERSEDED 2026-10-21 by `CONTENT-ECQI-LOAD-UNCHANGED`: no edit at all; the measurement period is a run-time parameter (`LANG-PARAMETER`). |
| `CONTENT-PIN-LOCK` | A tracked lock file recorded the hashes of fetched eCQI packages. SUPERSEDED 2026-10-21 by `CONTENT-ECQI-USER-PACKAGE`: there is no tracked lock. |
| `CONTENT-RUN-ALL-DEFAULT` | Content-ingest runs everything by default (measures, bundled expansions, test cases). Skip flags opt out. SUPERSEDED 2026-10-21 by `CONTENT-ECQI-USER-PACKAGE`: the import loads the one package the user names, and its value sets unless `--no-valuesets`. |
| `CONTENT-SHARE-FAITHFUL` | Published knowledge stays faithful to FHIR spec including where inconvenient (base64 stays base64). Ergonomics live downstream of the share. Consumers derive optimized forms. |
| `CONTENT-VSAC` | Fetches ValueSet expansions from NLM VSAC FHIR API; writes valueset(valueset_id, system, code) matching engine binding contract. Writes to VSAC authority schema. Amended 2026-10-21: the import expands the value sets the package's libraries name, with the user's own `UMLS_API_KEY`, and a value set that fails to expand fails the import. |
| `CONTENT-VSAC-AUTHORITY` | ValueSet expansions in own authority schema (catalog.vsac) to avoid duplication when multiple programs reference same ValueSets. valueset_ref stays with measure authority. |

## Synthetic data

The synthetic patient generator and the populations it produces.

| ID | Requirement |
|---|---|
| `DATA-SYNTH-LARGE` | The synthetic population large data source (`synthetic-population-large`, schema `synthetic_population_large`): about a million patients generated by the patient data synthesizer at realistic rates, 250,000 requested per pinned measure (`loader/docs/synthetic-data.md`), with one patient per manifest row carrying expected results. Managed source, never the default. Amended 2026-10-21: the cohorts are re-synthesized from the repository's own measures; with one pinned sample measure the source holds about 250,000 patients, and earlier cohorts generated from official measures are removed. Amended 2026-10-21 by `DATA-SYNTH-SMALL`: with the four pinned sample measures the source holds about 1,000,000 patients. |
| `DATA-SYNTH-MARKETPLACE` | Not built. Planned after v0.3 (roadmap `DATA-SYNTH-MARKETPLACE`). A pre-generated synthetic population (`DATA-SYNTH-LARGE`), on the `DATA-CONTRACT` layout with synthetic markers (`SYNTH-PDS-SYNTHETIC-TAG`), is published as a free Databricks Marketplace listing. A deployment installs it as a catalog and tags its schema `cql_databricks_data_source` (`DATA-SOURCE-UC-TAG`), so it is demoable from the first deploy without generating data in the workspace. One population serves every measure, so a shared library's results are computed once (`DEC-LIBRARY-OWNS-RESULTS`). The repository holds the generation Job, the install-and-tag step and the docs; the listing's provider profile is the publisher's. |
| `DATA-SYNTH-SMALL` | Added 2026-10-21. The synthetic population small data source (`synthetic-population-small`, schema `synthetic_population_small`): roughly 1,000 patients per pinned sample measure, defined in the synthesizer and run at a small scale to get people started easily. It is deployed with the project by default, not something they have to synthesize. It is the one sample set and the default data source, replacing `sample-patients`. It is plausible, comprehensive data with no answer key; the answer keys are the conformance tests' (`compiler/conformance`). |
| `SYNTH-MODULE` | The compiler ships a synthetic data generation module: realistic synthetic healthcare data across multiple standards (FHIR first, generated from measure logic by `SYNTH-PDS-MODULE`), landed on the same `data-contract` so a generated source is indistinguishable from a customer source to the binding path. |
| `SYNTH-PDS-CORRELATED` | The solver instantiates related resources and solves temporal relations by pinning slots, supporting measures with multiple encounters, extension slots, and constraints on value sets or null values. |
| `SYNTH-PDS-DETERMINISM` | All randomness flows from one seed via `(seed, patient, site)` keying. Identical (measure, config, seed) reproduce byte-identical NDJSON and manifest; rows carry no wall-clock timestamp. |
| `SYNTH-PDS-ECQM` | The synthesizer handles multi-library measures with a full multi-library ELM interpreter, solving population logic via declared heuristic strategies and verifying 100% of patients through the full ELM. Amended 2026-10-20: the official measures it was built for were removed; it is re-verified on the measures written for this repository. |
| `SYNTH-PDS-FIXTURE-PINS` | Measure fixtures (ELM and valueset expansions) are generated on the developer's machine and pinned by SHA-256 in `cql_databricks_synth/pins.json`; the cache is rebuilt and verified against the pins at test time. |
| `SYNTH-PDS-IR-CONSUMER` | The synthesizer reads measure semantics from the Compiler's ELM only, walking the same ELM the engine compiles. It fails loud on unsupported constructs rather than approximating. |
| `SYNTH-PDS-MEASURE-CONFIG` | Added 2026-10-21. A measure's own realism lives in an optional `<Measure>.synth.json` beside its CQL, pinned with it (`SYNTH-PDS-FIXTURE-PINS`): population rates, weights over the branches of an `or` define, a plausible range per declared code or value set, and weights over value-set members. Every key resolves against the measure's ELM or the load fails naming every unresolved key; a weight is positive, so every route and member stays reachable. It applies in the realistic mix only, a rate flag overrides it for one run, and its hash is part of the cohort identity. Adding a measure never edits synthesizer code. Amended 2026-10-21: a route branch may be a define of an included library, named by the alias the measure includes it as (`Common.In Hospice Care`). |
| `SYNTH-PDS-MODULE` | The patient data synthesizer generates coverage-directed synthetic cohorts from measure logic: enumerate population strata, solve constraints into skeleton patients, emit FHIR bundles with ground truth known by construction. Python command-line tool, offline, file-based. |
| `SYNTH-PDS-ORACLE` | Every generated patient carries an expected-results manifest row: the expected value of every define (true/false/null/value), computed by evaluating the bundle through ELM. Rows join against Runtime output for oracle-based validation. |
| `SYNTH-PDS-PRIORS` | Where measure logic leaves a choice open, realistic mode draws it from a distributional prior keyed to FHIR data or terminology, never to measure-specific rates, reweighting choices while keeping every route reachable. Amended 2026-10-21: measure-specific facts (route mix, quantity ranges, member weights) come only from the measure's config (`SYNTH-PDS-MEASURE-CONFIG`); the priors module holds no per-code range table. |
| `SYNTH-PDS-SCALE` | Cohort composition is derived from per-stratum count plans in three mix modes (uniform, realistic, explicit), and generation streams to files bounded by disk rather than memory, with 100% verification inline. Amended 2026-10-21: the realistic mix takes each rate from its flag, else the measure's config (`SYNTH-PDS-MEASURE-CONFIG`), else the default. |
| `SYNTH-PDS-SKIPPED` | A route the solver cannot invert is skipped when a sibling route satisfies the same criterion; skips are recorded per stratum in `StratumOutcome.skipped_routes` and reported as patient counts in `coverage.md`. |
| `SYNTH-PDS-STRATA` | Strata are enumerated by scoring type (funnel first: IPP, denominator, excluded, numerator hit/miss, null membership). Contradictory combinations are reported `unsatisfiable` with conflicting constraint sites. |
| `SYNTH-PDS-SYNTHETIC-TAG` | Every emitted resource carries synthetic markers: `meta.security` set to FHIR security marker plus a generator-version `meta.tag`, so synthesizer data cannot be mistaken for real data downstream. |
| `SYNTH-PDS-TERMINOLOGY` | The synthesizer generates code from the same valueset expansions the runtime binds, so code membership is asserted at generation time and generator and engine never disagree. Amended 2026-10-20: the expansions are the shipped measures' own value sets, not VSAC. |
| `SYNTH-PDS-VERIFY` | After fill, every patient (100%, never a sample) is re-evaluated through the same ELM and must land in its target stratum. Mismatch is a hard failure, never a quiet re-roll. |
| `SYNTH-SEED-OUT` | Data-source seeding leaves the synthesizer; seed scripts move to `scripts/` as a separate loader feature. |

## Data sources

The data sources the loader lands.

| ID | Requirement |
|---|---|
| `CONFORMANCE-SCHEMA` | Conformance data source is provisioned by the deploy and provides fixed test data for validation of library evaluation logic. Amended 2026-10-11 by `EMIT-ROOT-INDEPENDENT`: one conformance data source per measure the corpus covers (`DATA-CONFORMANCE-MEASURE-SELECT`). SUPERSEDED 2026-10-21 by `CONTENT-SAMPLE-PRELOAD` and `DATA-SAMPLE-SOURCE`: the per-measure conformance sources were built from the official test cases fetched from GitHub, which are removed. The deploy now lands the sample patients as one data source, and the compiler's conformance corpus stays internal to `compiler/`. |
| `DATA-CONFORMANCE-CORPUS` | Conformance data is a shared, consolidated corpus in one `conformance` schema with one mount table per FHIR type, shared terminology, and golden results. A deck is a `(deck, version)` row slice; install is `MERGE`, uninstall is `DELETE WHERE deck=…`. SUPERSEDED 2026-10-21 by `DATA-SAMPLE-SOURCE`: the deck corpus existed only for published test content, which is removed. |
| `DATA-DEMO-POPULATION` | A demo data source with a fabricated hypertensive population at realistic control rates, for showing plausible measure results. Kept separate from conformance to avoid desynchronizing the answer key. Data source `synthetic-population-small`, schema `synthetic_population_small`. SUPERSEDED 2026-10-21: the demo population and its generator were built on an official measure's shape and are removed. Synthetic populations are re-synthesized from the repository's own measures (`DATA-SYNTH-LARGE`), and the sample patients are `DATA-SAMPLE-SOURCE`. Its tag and schema name are reused by `DATA-SYNTH-SMALL`. |
| `DATA-SAMPLE-SOURCE` | The sample patients in `examples/data` are one data source, `sample-patients` (schema `sample_patients`), landed by the deploy through the shared population lander (`content_ingest.population`): one mount and one retrieve UDTF per bound FHIR type, empty where the samples have no rows, with its change logs and data-source tag. Fabricated, and its description says so. Added 2026-10-21. SUPERSEDED 2026-10-21 by `DATA-SYNTH-SMALL`: one sample set. The 48 hand-written patients remain only as conformance fixtures. |

## Retired

| ID | Status |
|---|---|
| `CONTENT-FHIR-IG` | Retired: IG packages are no longer fetched from CI builds. See `CONTENT-IG-ENGINE-ALIGN`. |
| `CONTENT-STRUCT-RENDER-FIDELITY` | Retired: resources are stored as `VARIANT`, which drops nothing. See `CONTENT-FHIR-VARIANT`. |
| `CONTENT-STRUCT-ROUNDTRIP` | Retired: resources are stored as `VARIANT`, which drops nothing. See `CONTENT-FHIR-VARIANT`. |
| `DATA-SYNTH-1M` | SUPERSEDED 2026-10-11 by `DATA-SYNTH-LARGE`: renamed, because the population grows with each measure's cohort, so a count in its name goes stale. |
| `DATA-SYNTHEA-SOURCE` | SUPERSEDED 2026-10-11 by `SYNTH-PDS-MODULE`: the synthesizer generates the synthetic sources from measure logic, and nothing used Synthea. The importer's Synthea path and the Synthea seeder are removed. |
| `MOUNT-MEASURE-NAME-MAP` | Retired: the published name is stored at ingest. See `CONTENT-MEASURE-NAME-PUBLISHED`. |
| `SYNTH-DETERMINISM` | Retired: Proposed, see `SYNTH-PDS-DETERMINISM`. |
| `SYNTH-FORMAT-EMITTER` | Retired: Proposed multi-format support. Not implemented. |
| `SYNTH-MEASURE-PROFILE` | Retired: Proposed generation profiles. Not implemented. |
| `SYNTH-REALISM` | Retired: Proposed realism layer. Not implemented. |
| `SYNTH-ROUNDTRIP` | Retired: Proposed cross-format parsing. Not implemented. |
| `SYNTH-SINGLE-SOURCE` | Retired: Proposed design principle. Not implemented. |
