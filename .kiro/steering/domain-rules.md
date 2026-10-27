---
inclusion: fileMatch
fileMatchPattern: "apps/**|packages/**|content/**|**/domain/**|**/application/**|**/*.mdx"
---

# Domain Rules

This steering file is the single source of truth for the EDU-IT domain rules: unlocking,
quizzes, labs, projects, mastery, review, hints, and placement. It is conditionally included
for domain and application code and for content (fileMatch inclusion). The domain rules below
are transcribed verbatim from the project rules; all numbers and normative wording are
preserved unchanged. Where a rule is not self-explanatory, a one-line reason is added and
marked `Reason:`.

## Progression and Unlocking

- Learners rebuild skills step by step and advance only by proving mastery.
  Reason: progression is gated on demonstrated mastery, not on time spent or content viewed.
- Mastery is proven through three mechanisms: quizzes, hands-on labs (real terminals, graded
  code) and projects.
- Decay never relocks content; it only re-queues for review.

## Mastery

- Mastery is `mastery` (0–100 per user × concept).
- Mastery states and thresholds:
  - `Unseen` → `Introduced`: first encounter (< 30).
  - `Introduced` → `Practiced`: 30-59.
  - `Practiced` → `Proficient`: 60-84.
  - `Proficient` → `Mastered`: >= 85 + lab check (if lab covers) + passed review.
- `Mastered` → `Proficient`: decay (never below 30 once Practiced).
- `Proficient` → `Practiced`: decay (never below 30).

## Review

- A concept enters the spaced review schedule on first reaching Practiced.
- Decay = -1/day overdue, deterministic from the clock.
- Decay never relocks content; it only re-queues for review.

## Hints

- Hint reveals are recorded (`hint_reveal`).
  Reason: revealing a hint is tracked as a mastery event so hint use is reflected in the
  learner's progress and in the worked mastery example.

## Quizzes

- Duplicate quiz submissions are deduped by an idempotency key; re-answering the same question
  within 24 hours of the first answer yields no additional mastery gain; the best attempt still
  counts for completion.

## Labs

- Labs are hands-on: real terminals, graded code.
- Grading is bounded: results within a 15 s target, executed at most once per 10 s per session.
- A lab check contributes to reaching `Mastered` where a lab covers the concept
  (`>= 85 + lab check (if lab covers) + passed review`).

## Projects

- Projects are one of the three mechanisms through which mastery is proven.
- Project submissions are reviewed through the `ProjectReviewer` extension point: mentor, peer
  or AI-assisted review.

## Placement

- Placement is supported (`placement`, schema and interfaces only for now).
- `module_progress` records `tested_out`.
  Reason: a learner who proves mastery at placement can test out of a module rather than
  working through it step by step.
