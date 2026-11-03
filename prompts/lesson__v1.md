---
output_schema: LessonDraft
input_kind: lesson
includes: glossary@1
---
# Task: draft one lesson

You write one lesson for IT beginners. Use the input fields `track`, `module`, `concept`, `outline`
(the lesson's entry), `sources` (reference material, if any) and `glossary`.

Return one JSON object with exactly these fields:

- `title`: `{ "en", "fr" }`.
- `sections`: a non-empty ordered list. Each section has `heading` and `body` (both `{ "en", "fr" }`)
  and `commands`, a list of shell command lines (possibly empty).

Rules:

- Commands are command lines only. Never write the output a command prints. The output is produced
  later by running the command in a sandbox, and any output you write is discarded.
- Every command must be safe to run as an unprivileged user in a throwaway container, must finish
  quickly and must not need network access.
- Write every text in both English and French with the same meaning.
- Use the glossary term pairs exactly. English text uses `term_en`, French text uses `term_fr`.
- Teach only what the `concept` needs. Do not invent sources or tool behaviour.
- Return JSON only. Do not add identifiers, provenance or reviewer fields.
