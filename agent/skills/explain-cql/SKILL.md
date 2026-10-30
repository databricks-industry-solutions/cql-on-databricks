---
name: explain-cql
description: "How to explain a define to a clinician from its actual CQL, in plain prose: quote the CQL, then say who it includes or removes and when. Load it when a user asks what a criterion or population means, such as \"what was the exclusion criteria?\"."
---

<!--
  AGENT-DEFINITION-DISCOVERY, AGENT-CQL-REVIEW — explaining a define to a clinician, from its CQL.
  A runtime asset: the agent loads it verbatim through get_skill. For the Designer's narration
  contract, see clinical-explain instead.
-->

# Explaining CQL to a clinical audience

The user asks what a criterion means: "What was the exclusion criteria?" Explain the actual CQL in
plain clinical language.

## How

1. Call `get_definition`. Find the define the user means. "The exclusion criteria" is usually the
   `Denominator Exclusions` define. Follow the defines it references when they carry the meaning.
2. Quote the CQL of that define, in a `cql` code block, so the user can check your reading against it.
3. Then explain it in plain prose for a clinician:
   - Who it includes or removes, in clinical terms: "patients in hospice care during the year".
   - Each condition it tests, in the order a clinician would check them.
   - Time windows, in words: "any time during the measurement year", "in the 12 months before".
   - What a value set stands for, by its declared name: "a diagnosis from the Diabetes value set".

## Rules

- Explain only what the CQL says. If something is not in the CQL, say it is not there.
- Do not use CQL, ELM or FHIR jargon in the explanation. "Retrieve", "define", "interval" and "query"
  are not clinical words.
- Do not state figures here. Counts come from `get_population_summary` or an experiment, labelled as
  such.
- Keep it short: a few sentences or a short list per define.
