"""Headless CLI for the Patient Data Synthesizer (SYNTH-PDS-MODULE).

    # uniform/adversarial mix (conformance): N patients per satisfiable stratum
    python -m cql_databricks_synth generate \
        --elm <library.elm.json> --valuesets <dir-of-valueset-*.ndjson> \
        --out <dir> [--seed 42] [--per-stratum 1]

    # realistic mix (demo/load): total size + funnel rates -> per-stratum counts; the measure's own
    # realism from its config (SYNTH-PDS-MEASURE-CONFIG), each rate flag overriding the config
    python -m cql_databricks_synth generate ... \
        --patients 1000000 [--config <Measure>.synth.json] [--out-of-ipp-rate 0.30] \
        [--exclusion-rate 0.05] [--numerator-rate 0.72] [--null-rate 0.01]

    # explicit mix: exact counts per stratum label
    python -m cql_databricks_synth generate ... --count 'IPP+DEN+!DENEX+NUM=70' ...

    python -m cql_databricks_synth verify \
        --elm <library.elm.json> --valuesets <dir> --out <dir>

    # a population from a recipe: one realistic cohort per pinned measure, merged, resources only (DATA-SYNTH-SMALL)
    python -m cql_databricks_synth population --recipe loader/synthesizer/recipes/synthetic-small.json --out examples/data

Binding an emitted cohort as the Synthetic Population Large data source (DATA-SYNTH-LARGE) is not this
CLI's job since SYNTH-SEED-OUT: it is ``python loader/scripts/synthetic-population/load.py``, the same step a
customer runs on their own data, built from the evaluator's seed contract.

Generation is file-based and offline by design (no network egress): ELM comes from the
Compiler Service's /translate ahead of time, expansions from the deck/UC-exported NDJSON
shape. Nothing here talks to a workspace except ``fixtures``, for UC-sourced expansions. Generation streams to disk, so cohort size is
bounded by disk, not memory (SYNTH-PDS-SCALE).
"""

from __future__ import annotations

import argparse
import json
import sys

from . import SynthesizerError
from .generate import generate_to_dir, uniform_counts
from .strata import DEFAULT_RATES, population_defines, proportion_strata, realistic_counts, realistic_rates
from .valuesets import ValueSetIndex
from .verify import verify_cohort


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--elm", required=True,
                        help="the library's ELM JSON, or a directory of *.elm.json for a "
                             "multi-library closure (root inferred)")
    parser.add_argument("--valuesets", required=True,
                        help="directory of valueset-*.ndjson expansions")
    parser.add_argument("--out", required=True, help="cohort output directory")


def _load_elm(path: str):
    from pathlib import Path

    from .library import Env

    if Path(path).is_dir():
        return Env.from_paths([path])
    with open(path) as fh:
        return json.load(fh)


def _rates(args, config) -> dict[str, float]:
    """Each realistic-mix rate: its flag, else the measure's config, else the default."""
    return realistic_rates(config, {name: getattr(args, f"{name.removesuffix('_rate')}_rate")
                                    for name in DEFAULT_RATES})


def _counts(args, elm: dict, config) -> dict[str, int]:
    """The per-stratum plan from whichever mix mode the flags select."""
    populations = population_defines(elm)
    strata = proportion_strata(populations)
    modes = sum(1 for flag in (args.patients, args.count) if flag)
    if modes > 1:
        raise SynthesizerError("--patients and --count are separate mix modes; pick one")
    if args.count:
        counts = {}
        for item in args.count:
            label, _, number = item.rpartition("=")
            if not label or not number.isdigit():
                raise SynthesizerError(f"--count expects LABEL=N, got {item!r}")
            counts[label] = int(number)
        return counts
    if args.patients:
        return realistic_counts(strata, populations, args.patients, **_rates(args, config))
    return uniform_counts(strata, args.per_stratum)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m cql_databricks_synth")
    commands = parser.add_subparsers(dest="command", required=True)

    gen = commands.add_parser("generate", help="generate a cohort with known ground truth")
    _common(gen)
    gen.add_argument("--seed", type=int, default=42)
    gen.add_argument("--measurement-period", metavar="START/END",
                     help="run-time Measurement Period as inclusive ISO dates, e.g. "
                          "2026-01-01/2026-12-31 (LANG-PARAMETER); required when the measure "
                          "declares no default")
    gen.add_argument("--per-stratum", type=int, default=1,
                     help="uniform mode: patients per satisfiable stratum")
    gen.add_argument("--patients", type=int,
                     help="realistic mode: total cohort size shaped by the rate flags")
    gen.add_argument("--config", metavar="PATH",
                     help="realistic mode: the measure's <Measure>.synth.json (SYNTH-PDS-MEASURE-CONFIG)")
    gen.add_argument("--out-of-ipp-rate", type=float)
    gen.add_argument("--exclusion-rate", type=float)
    gen.add_argument("--numerator-rate", type=float)
    gen.add_argument("--null-rate", type=float)
    gen.add_argument("--exception-rate", type=float)
    gen.add_argument("--count", action="append",
                     help="explicit mode: 'STRATUM_LABEL=N', repeatable")

    ver = commands.add_parser("verify", help="re-verify an emitted cohort against its manifest")
    _common(ver)

    population = commands.add_parser(
        "population", help="build a population from a recipe of pinned measures (DATA-SYNTH-SMALL)")
    population.add_argument("--recipe", required=True, help="the recipe JSON (see recipe.py)")
    population.add_argument("--out", required=True,
                            help="output directory; every *.ndjson in it is replaced")

    fixtures = commands.add_parser(
        "fixtures", help="(re)generate the pinned measure fixtures into the local cache")
    fixtures.add_argument("--measure", action="append",
                          help="measure key from pins.json; repeatable (default: all)")
    fixtures.add_argument("--endpoint",
                          default=None, help="Compiler Service (default: $CQL_COMPILER_ENDPOINT "
                                             "or http://127.0.0.1:8080)")
    fixtures.add_argument("--profile",
                          help="Databricks profile for UC-sourced expansions (default: databricks.local.yml, then DATABRICKS_CONFIG_PROFILE)")
    fixtures.add_argument("--catalog",
                          help="catalog holding the pinned terminology table (default: $CONTENT_CATALOG, "
                               "set from databricks.local.yml by scripts/with-local-env.sh)")
    fixtures.add_argument("--repin", action="store_true",
                          help="record observed hashes into pins.json instead of enforcing them")

    args = parser.parse_args(argv)

    if args.command == "fixtures":
        import os

        from cql_databricks_common.databricks_profile import databricks_profile

        from .fixtures import build, load_pins, write_pins

        pins = load_pins()
        endpoint = args.endpoint or os.environ.get("CQL_COMPILER_ENDPOINT") \
            or "http://127.0.0.1:8080"
        profile = databricks_profile(args.profile)
        catalog = args.catalog or os.environ.get("CONTENT_CATALOG")
        failures = 0
        for measure in args.measure or sorted(pins):
            try:
                result = build(measure, endpoint=endpoint, profile=profile, catalog=catalog,
                               repin=args.repin, pins=pins)
                print(f"  {measure}: {result.libraries} libraries, "
                      f"{result.valuesets} expansions -> {result.directory}")
            except SynthesizerError as e:
                failures += 1
                print(f"  {measure}: FAILED — {e}")
        if args.repin and not failures:
            write_pins(pins)
            print("  pins.json updated")
        return 1 if failures else 0

    if args.command == "population":
        from .recipe import Recipe, build_population

        written = build_population(Recipe.from_file(args.recipe), args.out)
        for fhir_type, count in written.items():
            print(f"  {fhir_type}: {count}")
        print(f"  wrote {args.out}")
        return 0

    elm = _load_elm(args.elm)
    valuesets = ValueSetIndex.from_ndjson_dir(args.valuesets)

    if args.command == "generate":
        # SYNTH-PDS-PRIORS is bound to the mix mode, not a separate flag: realistic mode
        # exists to look like a population, uniform/adversarial exists to cover every
        # domain member. Shaping the second would defeat the reason to ask for it.
        config = None
        if args.config:
            if not args.patients:
                raise SynthesizerError("--config shapes the realistic mix only; pass --patients")
            from .library import Env
            from .measure_config import load

            config = load(args.config, elm if isinstance(elm, Env) else Env([elm]), valuesets)
        meta, outcomes = generate_to_dir(
            elm, valuesets, args.out, seed=args.seed, counts=_counts(args, elm, config),
            priors=bool(args.patients), measurement_period=args.measurement_period, config=config)
        satisfied = [o for o in outcomes if o.status == "satisfied"]
        unsat = [o for o in outcomes if o.status == "unsatisfiable"]
        total = sum(o.patient_count for o in outcomes)
        print(f"cohort {meta.cohort_id}: {total} patients, "
              f"{len(satisfied)} strata satisfied, {len(unsat)} unsatisfiable")
        for outcome in outcomes:
            note = f" ({outcome.detail})" if outcome.status == "unsatisfiable" else ""
            print(f"  {outcome.stratum}: {outcome.patient_count}{note}")
        print(f"  wrote {args.out}")
        return 0

    mismatches = verify_cohort(elm, valuesets, args.out)
    if mismatches:
        for mismatch in mismatches:
            print(f"MISMATCH {mismatch.patient_id} {mismatch.define!r}: "
                  f"manifest={mismatch.expected} recomputed={mismatch.actual}")
        return 1
    print("verified: every manifest row reproduces")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SynthesizerError as e:
        print(f"synthesizer: {e}", file=sys.stderr)
        sys.exit(2)
