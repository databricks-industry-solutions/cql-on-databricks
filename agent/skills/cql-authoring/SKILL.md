---
name: cql-authoring
description: "How to write and edit CQL that a measure author would accept: smallest edits, reusable defines, fluent functions, temporal operators, value sets versus direct codes, population names, and the compiler gaps to avoid. Load it before writing any CQL."
---

<!--
  APP-AUTHORING-SKILL, AGENT-CQL-REVIEW — how the agent writes and edits CQL for an experiment.
  A runtime asset: the agent loads it verbatim through get_skill.
-->

# Writing CQL for an experiment

Write CQL a measure author would accept: small, readable, and checked before it runs.

## Editing an existing definition

- Change as little as possible. Edit the one define the user asked about; leave the others as written.
- Keep the `library` header, the `using` line, every `include` and every declaration you do not need
  to change.
- Remove a criterion by deleting its condition from the `where` or the `and` / `or` chain. Do not
  replace it with `true` or comment it out.
- Do not rename defines. The populations are found by their standard names: `Initial Population`,
  `Denominator`, `Denominator Exclusions`, `Denominator Exceptions`, `Numerator`,
  `Numerator Exclusions`.

## Conventions

- **Reusable defines.** Give a condition a define of its own when two populations use it, or when it
  has a clinical name ("Has Hospice Services").
- **Fluent functions** read well for a step on one resource (`Encounter.isFinished()`). Use the ones
  the included libraries declare. Do not invent new ones for a one-off.
- **Temporal operators.** Say what the clinician means: `during "Measurement Period"`,
  `starts before end of`, `within 12 months of`. Prefer these to comparing dates by hand.
- **Value sets and direct codes.** Use a value set when one exists for the concept. Use direct codes
  only for a code list the user gave you:

  ```cql
  codesystem "ICD10CM": 'http://hl7.org/fhir/sid/icd-10-cm'
  code "Type 2 diabetes without complications": 'E11.9' from "ICD10CM" display 'Type 2 diabetes mellitus without complications'

  define "Diabetes Diagnosis":
    [Condition: "Type 2 diabetes without complications"]
  ```

  For several codes, declare each and union the retrieves.
- **Every define after `context Patient`.** The compiler runs a whole library in one context.

## Before running

- Call `check_cql` and fix every diagnostic.
- Show the user the diff, or offer to (`experiment`, step 3).

## Known compiler gaps

Avoid these forms. They fail, or give a wrong answer with no error:

- A FHIR choice value (`Observation.effective`) passed to a function whose parameter is a choice. Pass
  the resource instead.
- `sort by` a choice element or a fluent call on `$this`.
- Two functions with one name and different argument types.
- `year from X`. Use interval arithmetic on the period.
- String operators other than `Upper`, `Lower`, `Length`, `StartsWith`, `EndsWith` and `Split` on a constant
  separator: `Substring`, `PositionOf`, concatenation with `&` or `+` and the rest. Indexing a string
  (`'abc'[0]`) fails too.
