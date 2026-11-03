---
output_schema: ProjectDraft
input_kind: project
includes: glossary@1
---
# Task: draft a module project

You write the capstone project of a module, with the rubric that scores it. Use the input fields
`track`, `module`, `concepts`, `outline`, `rubric` (author guidance, if any) and `glossary`.

Return one JSON object with exactly these fields:

- `title` and `description`: `{ "en", "fr" }`. The description is the project brief a learner follows.
- `criteria`: a non-empty list. Each criterion has `title` (`{ "en", "fr" }`) and `max_points`, a
  positive integer weight.

Rules:

- The project uses only the concepts listed in `concepts`.
- Each criterion is observable and scored independently of the others. Weights reflect importance.
- Write every text in both English and French with the same meaning.
- Use the glossary term pairs exactly. English text uses `term_en`, French text uses `term_fr`.
- Return JSON only. Do not add identifiers, provenance or reviewer fields.
