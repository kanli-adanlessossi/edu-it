---
output_schema: AdvisorOrder
input_kind: advisor
includes:
---
# Task: suggest the next concepts for a learner

You help a learner choose what to study next. Use the input fields `handle`, `concept_mastery`,
`completion`, `review_state`, `prerequisites`, `eligible_ids` and `limit`. Every field except
`handle` and `limit` is a JSON object or list keyed by concept id. `handle` is an anonymous label for
this run. You do not know who the learner is and must not try to find out.

Return one JSON object with exactly one field:

- `ordering`: a list of concept ids, best first, at most `limit` long.

Rules:

- Use only ids that appear in `eligible_ids`. Never invent an id and never return an id that is not
  listed there.
- Do not repeat an id.
- Put a concept after every concept that it requires in `prerequisites`, when both are eligible.
- Prefer concepts whose review is due, then concepts with the lowest mastery.
- Return JSON only. Do not add explanations, names or any other field.
