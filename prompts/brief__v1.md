---
output_schema: BriefDraft
input_kind: brief
includes: glossary@1
---
# Task: draft a track brief

You write the brief of a learning track for IT beginners. Use the input fields `track`, `brief` (the
author's notes, if any) and `glossary`.

Return one JSON object with exactly these fields:

- `title`: `{ "en": ..., "fr": ... }`, the track title.
- `description`: `{ "en": ..., "fr": ... }`, a short summary of the track.
- `learning_outcomes`: a non-empty list of `{ "en": ..., "fr": ... }`, what a learner can do afterwards.

Rules:

- Write every learner-facing text in both English and French. The two texts carry the same meaning.
- Use the glossary term pairs exactly. English text uses `term_en`, French text uses `term_fr`.
- State only facts you are sure of. Do not invent sources, versions or commands.
- Return JSON only. Do not add identifiers, provenance, reviewer fields or any other field.
