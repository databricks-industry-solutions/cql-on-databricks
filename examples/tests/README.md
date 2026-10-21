# Live deployment tests

Both tests here answer one question: **does the deployed system actually work?** They are the only tests
in the repository that need a real Databricks workspace, which is why they live together and away from
everything else.

| Test | What it proves |
|---|---|
| `test_deployed_compiler_smoke.py` | The minimal case. Client → deployed Compiler Service App → emitted SQL → local Spark, asserting `1 + 1 = 2`. If this fails, nothing below it can pass. |
| `test_examples_run.py` | The full cases. Submits every notebook in `examples/` as a serverless job run and asserts each exits `{"ok": true}`. Stops the examples silently rotting. |
| `test_release_live.py` | The release check (`CLIENT-RELEASE-TESTS`). Submits `release/measure_release.py` as a serverless job run. It installs the client from the Volume, discovers the measure and the data source by tag, computes the measure durably, compares each population with its answer key per test patient, then checks a second run reuses every define. |

They sit under `examples/` because the examples **are** the exemplar of a working deployment: the smoke
test is the floor, the notebooks are the ladder.

Both **skip cleanly** without a configured workspace, so they never break a hermetic run. Run them
against your deployed workspace with:

```bash
scripts/test.sh integration
```

It reads the profile from `databricks.local.yml`, resolves the runtime Volume, the compiler App's URL and a
token, and sets the variables both tests read. A skipped test fails that run.

They are deliberately **not** part of `./gradlew checkAll`. That gate is hermetic — no secrets, no
workspace, runs on a fork — and these cannot be.
