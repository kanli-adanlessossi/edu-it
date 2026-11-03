---
output_schema: QuizDraft
input_kind: quiz
includes: glossary@1
---
# Task: draft a lesson quiz

You write a quiz that checks one lesson. Use the input fields `track`, `module`, `concept`, `lesson`,
`difficulty` and `glossary`.

Return one JSON object with exactly these fields:

- `title`: `{ "en", "fr" }`.
- `questions`: a non-empty list. Each question has `prompt_text`, `explanation` and `concept_title`
  (all `{ "en", "fr" }`) and `options`, a list of at least two options. Each option has `text`
  (`{ "en", "fr" }`) and `is_correct` (boolean).

Rules:

- Every question has at least one correct option, and the correct answer must be clearly right.
- Wrong options are plausible but plainly wrong. Do not use "all of the above" or "none of the above".
- The explanation says why the correct option is right.
- Ask only about what the lesson teaches. Each question names the concept it checks, using the
  concept title word for word.
- Write every text in both English and French with the same meaning.
- Use the glossary term pairs exactly. English text uses `term_en`, French text uses `term_fr`.
- Return JSON only. Do not add identifiers, provenance or reviewer fields.
