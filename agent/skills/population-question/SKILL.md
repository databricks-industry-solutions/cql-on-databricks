---
name: population-question
description: "The workflow for a question about a population's size or score, asked in the user's own words: find candidate definitions and let the user choose, find a calculation already made and ask to reuse it, then report each population and the score. Load it when a user asks how many patients a population has, or how a measure scored."
---

<!--
  AGENT-SAVED-RESULTS, AGENT-DEFINITION-DISCOVERY — the workflow for "how big is this population?".
  A runtime asset: the agent loads it verbatim through get_skill.
-->

# Answering a population question

The user asks about a population in their own words: "How big is the diabetes population as of 2026?"
Answer it from a definition they chose and a calculation you can name. Never from a guess.

## 1. Find the definition, and let the user choose it

1. Call `list_definitions`. Read the titles and descriptions and pick the entries that mean what the
   user asked for. Match on meaning, not on exact words: "diabetes" matches "Diabetes: Glycemic Status
   Assessment Greater Than 9%".
2. Ask the user which one to use. Name each candidate by its title, library and version, with one line
   on what it counts. If exactly one fits, still ask: "I found X. Would you like me to use that?"
3. If none fits, say so, and offer to write a new definition (see the `experiment` and
   `cql-authoring` skills). Never pick one silently.
4. Any CQL you write for the user, review with them before it runs: show it, or at least offer to.

## 2. Settle the period

- The tools run over one set of data, chosen for you (`AGENT-DEFAULT-ENVIRONMENT`). Never ask the user
  which data to use.
- Turn the user's words into a measurement period. "As of 2026" is 2026-01-01 to 2026-12-31. Say
  which period you used.

## 3. Look for a calculation already made

Call `find_results` with the library, version and period.

- **Found:** tell the user when it was computed and ask whether to reuse it. "This was calculated on
  October 12, 2026. Use that?" Source freshness is not tracked: a computation time is not a statement
  about how current the data is.
- **Not found:** say so. Offer to run the definition inline with `run_cql`, using the CQL from
  `get_definition`. That result is computed now and kept nowhere.

## 4. Report it

Call `get_population_summary` with the `resultSet` from `find_results`.

- Report each population's count as written: the Initial Population, the Denominator, and the rest.
  These are the defines' own results.
- Report the score separately, with its own figures: "Performance rate 38.0%: 3,100 of 8,150, the
  Numerator within the Denominator less its exclusions." The score's numerator and base are counted
  inside the population cascade. The population counts are not, so they need not add up to it.
- Show the counts as a small table or chart (one bar per population) when your host can draw one.
- Say which definition, period and computation time the figures come from.

## Follow-up questions

- "What was the exclusion criteria?" Load `explain-cql`, then call `get_definition` and explain the
  define from its CQL.
- "What if we removed that?" Load `experiment`.
