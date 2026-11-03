---
output_schema: AnswerSelection
input_kind: verify_answer
includes:
---
# Task: answer a quiz question

You are an independent answerer. Answer the question using only your own knowledge. Use the input
fields `question`, `options` and `locale`. You are not told which option the author intended.

Return one JSON object with exactly this field:

- `selected`: the zero-based indexes of the options you judge correct, in ascending order, as a list
  of integers.

Rules:

- Select every option that is correct and no other option.
- Do not explain. Return JSON only.
