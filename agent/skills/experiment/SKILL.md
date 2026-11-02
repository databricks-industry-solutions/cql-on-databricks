---
name: experiment
description: "The workflow for changing a definition and comparing it with the original: remove a criterion, change the period, use a code list, or write a new definition. Covers the smallest edit, reviewing the CQL with the user before it runs, and reporting experiment figures apart from official ones. Load it before editing or running any CQL."
---

<!--
  AGENT-INLINE-EXPERIMENTS, AGENT-CQL-REVIEW — the workflow for "what if we changed the definition?".
  A runtime asset: the agent loads it verbatim through get_skill.
-->

# Running an experiment on a definition

The user wants to see what changes if the definition changes: remove an exclusion, widen the period,
use a different code list. You edit the CQL, the user reviews it, and `compare_cql` runs both versions.
Nothing is saved, so nothing official changes.

## 1. Start from the CQL as written

Call `get_definition` for the library the user chose. The `cql` it returns is the baseline. Keep it
unchanged: it is `baseline_cql` for every call below.

## 2. Make the smallest edit that does what the user asked

Load `cql-authoring` before you write CQL.

- **Remove a criterion:** delete that one condition from the one define. Leave every other define as is.
- **Change the period:** change nothing in the CQL. Pass `period_start` and `period_end`.
- **Use a code list:** declare the codes in the CQL (`codesystem`, `code`) and test membership against
  them in place of the value set. Do not invent codes: use the ones the user gave you, or look them up
  with `value_sets_with_code` and `list_value_sets`.
- **A new definition:** write a whole library, with a `library` header and the population defines.

Keep the library header as written when editing. Then `find_results`, the measure's declared scoring
and its includes all still apply.

## 3. Review the CQL with the user before running it

This step is not optional. Human review is what makes the result worth trusting.

1. Call `check_cql` with the edited CQL and `baseline_cql`. Fix every diagnostic it reports.
2. Show the user the `diff` it returns, the whole library for a new definition, and say in plain
   language what each change does. If the user would rather not read CQL, at least offer to show it.
3. Run only after the user agrees, or declines to look.

## 4. Run both and compare

Call `compare_cql` with `baseline_cql`, the edited `cql` and the period. It
recomputes both versions from the data as it is now, so every difference comes from the edit alone.

Report:

- Each population before and after, and how many patients entered and left it.
- The score before and after, each with its own numerator and base.
- That these are experiment figures, computed inline and not saved. Never call them the official
  result. The official result is the one `find_results` found, and it may be from an earlier date.

Then offer to show the exact CQL that ran. It is in the tool's answer.

## Notes

- A removed exclusion can leave a population's own count unchanged yet change the score. The score is
  counted inside the cascade and a population is not. Say so when it happens.
- Running is slower than reading a saved result: both versions are computed from source.
- To try another change, start again from `baseline_cql`, not from the last experiment, unless the
  user asks to build on it.
