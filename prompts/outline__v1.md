---
output_schema: OutlineDraft
input_kind: outline
includes: glossary@1
---
# Task: draft a track outline

You plan the modules and lessons of a learning track from its approved brief. Use the input fields
`track`, `brief`, `concepts` (existing concepts, if any) and `glossary`.

Return one JSON object with exactly this field:

- `modules`: a non-empty ordered list. Each module has `title` (`{ "en", "fr" }`) and `lessons`, a
  non-empty ordered list. Each lesson has `title`, `summary` and `concept_titles` (a list of
  `{ "en", "fr" }`), all bilingual.

Rules:

- Order modules and lessons so that every concept is taught after the concepts it depends on.
- Reuse the titles of existing concepts word for word when a lesson covers them.
- Write every text in both English and French with the same meaning.
- Use the glossary term pairs exactly. English text uses `term_en`, French text uses `term_fr`.
- Return JSON only. Do not add identifiers, provenance or reviewer fields.
