#!/usr/bin/env bash
# Regenerate the UI test fixtures derived from synthetic CQL (APP-SPEC-MODEL).
#
# `src/measures/__fixtures__/spec-patterns/ExampleReadingControl.cql` is the source: a Designer test fixture, not a
# sample measure. This translates it with the compiler's own
# CqlToElmCli (the translator the conformance corpus uses), then writes, keyed `root`:
#   spec-patterns-elm.json     {elm, source}: the ELM and the CQL it came from, as spec-model-elm.json does
#   spec-patterns-refset.json  the reference set the real extractor derives from that ELM (SPEC-EXPLAIN-INPUTS)
# Both are committed. Edit the CQL, then run this; never edit the JSON by hand.
set -euo pipefail
here="$(cd "$(dirname "$0")/../.." && pwd)"
fixtures="$here/designer/src/measures/__fixtures__"
cql="$fixtures/spec-patterns/ExampleReadingControl.cql"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "==> translating $(basename "$cql")"
"$here/gradlew" -p "$here" -q -I "$here/designer/scripts/translate-cql.init.gradle" \
  :compiler:conformance:translateCql -PcqlDir="$fixtures/spec-patterns" -PelmDir="$tmp"
[ -f "$tmp/ExampleReadingControl.json" ] || { echo "!!! no ELM produced for $cql"; exit 1; }

echo "==> deriving the reference set"
"$here/.venv/bin/python" - "$cql" "$tmp/ExampleReadingControl.json" "$fixtures" <<'PY'
import json, sys
from cql_databricks_designer.explain.reference_set import build_reference_set
cql_path, elm_path, out = sys.argv[1:]
elm = json.load(open(elm_path, encoding="utf-8"))
source = open(cql_path, encoding="utf-8").read()
with open(f"{out}/spec-patterns-elm.json", "w", encoding="utf-8") as fh:
    json.dump({"root": {"elm": json.dumps(elm, separators=(",", ":")), "source": source}}, fh, indent=2)
    fh.write("\n")
with open(f"{out}/spec-patterns-refset.json", "w", encoding="utf-8") as fh:
    json.dump({"root": build_reference_set(elm).to_dict()}, fh, indent=2)
    fh.write("\n")
print(f"    wrote {out}/spec-patterns-elm.json and spec-patterns-refset.json")
PY
