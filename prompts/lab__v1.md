---
output_schema: LabDraft
input_kind: lab
includes: glossary@1
---
# Task: draft a hands-on lab

You write one lab that a learner completes in a throwaway container. Use the input fields `track`,
`module`, `concept`, `lesson`, `image`, `kind` and `glossary`.

Return one JSON object with exactly these fields:

- `title` and `instructions`: `{ "en", "fr" }`.
- `kind`: the value of the input `kind`.
- `setup_script`: a shell script that builds the starting state, or null.
- `tasks`: a non-empty list. Each task has `title` and `description` (`{ "en", "fr" }`), `hints` (a
  list of `{ "en", "fr" }`, from gentle to explicit) and `checks`, a non-empty list. Each check has
  `script` (source of the check), `timeout_seconds` (a positive integer), `pass_message` and
  `fail_message` (`{ "en", "fr" }`).
- `reference_solution`: a script that solves every task from the starting state.

Rules:

- Every check must fail on the starting state and pass after the reference solution. Both are tested.
- A check inspects the resulting state, not the commands the learner typed.
- Scripts run as an unprivileged user, finish quickly and need no network access.
- Write every learner-facing text in both English and French with the same meaning.
- Use the glossary term pairs exactly. English text uses `term_en`, French text uses `term_fr`.
- Return JSON only. Do not add identifiers, provenance or reviewer fields.
