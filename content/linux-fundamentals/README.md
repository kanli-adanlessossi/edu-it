# Seed content — Linux Fundamentals (origin `ai`)

Authored seed content for the "Linux Fundamentals" track (task 14.1, Req 15.1–15.6).
These are **data files**, not code: one JSON file per catalog content type, each holding
a list of items that validate against the Pydantic v2 schemas in
`packages/content/edu_it_content/schemas.py`. The `edu-it content validate` command
(task 14.2) consumes these files; `content sync` (task 11) upserts them keyed by
`stable_id`.

## Files

| File | Content type | Items |
|------|--------------|-------|
| `tracks.json` | `track` | `track.linux-fundamentals` |
| `modules.json` | `module` | `module.files-permissions`, `module.processes` |
| `lessons.json` | `lesson` | `lesson.reading-permissions`, `lesson.changing-permissions`, `lesson.processes-intro` |
| `concepts.json` | `concept` | one per lesson area |
| `prerequisites.json` | `prerequisite` | Processes concept requires both Files & Permissions concepts |
| `quizzes.json` | `quiz` | `quiz.reading-permissions`, `quiz.changing-permissions` (one per F&P lesson) |
| `questions.json` | `question` | 5 per quiz; one command-output question per quiz |
| `question_options.json` | `question_option` | options for every question |
| `projects.json` | `project` | `project.harden-shared-directory` |
| `rubrics.json` | `rubric` | `rubric.harden-shared-directory` |
| `rubric_criteria.json` | `rubric_criterion` | correctness, least privilege, idempotency, explanation |

## Structure (content tree)

```
track.linux-fundamentals
├─ module.files-permissions (ordinal 0)
│  ├─ lesson.reading-permissions (ordinal 0)
│  │  └─ quiz.reading-permissions
│  │     ├─ question.rp.1-ls-l-fields        (ls -l / directory marker)
│  │     ├─ question.rp.2-rwx-meaning         (rwx meaning)
│  │     ├─ question.rp.3-octal-from-symbolic (octal <-> symbolic)
│  │     ├─ question.rp.4-group-read-true-false
│  │     └─ question.rp.5-command-output      (COMMAND-OUTPUT)
│  └─ lesson.changing-permissions (ordinal 1)
│     └─ quiz.changing-permissions
│        ├─ question.cp.1-chmod-symbolic
│        ├─ question.cp.2-chmod-octal
│        ├─ question.cp.3-chown-chgrp
│        ├─ question.cp.4-setgid-umask        (setgid on shared dir)
│        └─ question.cp.5-command-output      (COMMAND-OUTPUT, umask 022)
└─ module.processes (ordinal 1)  — requires Files & Permissions (via concept prerequisites)
   └─ lesson.processes-intro (ordinal 0)  — placeholder, NO quiz

project.harden-shared-directory
└─ rubric.harden-shared-directory
   ├─ rubric_criterion.correctness      (40)
   ├─ rubric_criterion.least-privilege  (25)
   ├─ rubric_criterion.idempotency      (20)
   └─ rubric_criterion.explanation      (15)

concepts: reading-permissions, changing-permissions, processes
prerequisites: processes -> reading-permissions, processes -> changing-permissions
```

## Provenance state

Every item carries `provenance.origin = "ai"`. The user approved the seed content: each item
records `reviewed_by = "kanli-adanlessossi"` (GitHub handle, to be replaced by the real
`user.stable_id` once accounts exist) and a `reviewed_at` timestamp, so `edu-it content
validate` passes the reviewer rule (Req 13.6).
Approval is supplied explicitly when syncing:
`edu-it-content sync --approved-by <user-stable-id> [--approved-at <ISO8601>]`
records the approver as `reviewed_by`/`reviewed_at` and marks the synced content as the
`Golden_Set` (Req 16.2, 16.3). Without `--approved-by` nothing is attributed or
marked golden (Req 16.4).

## Lesson bodies

Learner-facing titles/prompts/text are bilingual (`fr` + `en`) in these data files.
Lesson MDX bodies are authored separately per locale and rendered through the
component allowlist (ADR A5); they are not part of the catalog schema.
