---
output_schema: VariantDraft
input_kind: variant
includes: glossary@1
---
# Task: draft a variant of an approved question

You write one new question that checks the same concept as an approved parent question, so that a
learner who reviews the concept sees a fresh question instead of the same one. Use the input fields
`parent_question`, `parent_options`, `concept`, `locale`, `glossary` and `count`. `parent_options` is
a JSON list of the parent's option texts. You are not told which parent option is correct.

Return one JSON object with exactly these fields:

- `prompt_text`: `{ "en", "fr" }`.
- `explanation`: `{ "en", "fr" }`.
- `options`: a list of at least two options. Each option has `text` (`{ "en", "fr" }`) and
  `is_correct` (boolean).

Rules:

- Check the same concept as the parent, named in `concept`. Do not test anything the parent does not.
- Do not copy the parent. Change the scenario, the values or the wording, and keep the difficulty.
- At least one option is correct, and the correct answer must be clearly right.
- Wrong options are plausible but plainly wrong. Do not use "all of the above" or "none of the above".
- The explanation says why the correct option is right.
- Write every text in both English and French with the same meaning.
- Use the glossary term pairs exactly. English text uses `term_en`, French text uses `term_fr`.
- Return JSON only. Do not add identifiers, provenance, reviewer or state fields.
