# Data Model (D3)

This document (D3) is the authoritative source for the EDU-IT data model. It presents the full
schema as a Mermaid entity-relationship (ER) diagram, grouped into identity, catalog, provenance,
progress, mastery and review, AI/review ops, and assessments. It records which fields carry
mastery, review, and decay state; it fixes the `stable_id` + `content_version` identity model for
content; and it documents, at the data level, failure scenario 4.5 (content sync during active
use) and failure scenario 4.6 (duplicate quiz submission).

The authoritative privacy rules — export, deletion, and retention over every personal-data table —
live in steering and are not restated here; this document links them:

#[[file:.kiro/steering/auth-privacy.md]]

Where this document and the steering file describe the same rule, the steering file is
authoritative. This document adds the schema, the diagram, the per-table personal-data coverage,
and the data-level cross-references for reviewers.

_Related documents:_

- Architecture — `docs/ARCHITECTURE.md` (D1)
- API conventions (stable ID, idempotency, scenario 4.6) — `docs/API-CONVENTIONS.md` (D2)
- AI-content design (scenario 4.5 in coordination with this document) — `docs/AI-CONTENT-DESIGN.md` (D6)

The domain rules this schema encodes (mastery thresholds, review schedule, decay, hints, the
24-hour no-additional-mastery window) are in #[[file:.kiro/steering/domain-rules.md]].

---

## 1. Conventions

A few conventions apply across every entity in the schema.

### 1.1 Internal key vs. stable ID

- Every row has an internal primary key (`id`) that is **never exposed in the API**. It is an
  internal numeric/surrogate key used only for relational joins.
- Every API-addressable resource additionally carries a **stable ID** (`stable_id`): an immutable,
  opaque, prefixed, URL-safe string that never changes for the lifetime of the resource (for
  example `usr_01HZX9…`, `qz_01HZX9…`, `qa_01HZX9…`). The full stable-ID format and rules are in
  `docs/API-CONVENTIONS.md` (D2), Section 4. The `stable_id` is globally unique and is the sort
  tiebreaker for pagination and the identity anchor for idempotent content upserts.

### 1.2 Content identity: `stable_id` + `content_version`

Every content item carries **both**:

- a `stable_id` (identity) that **never changes** across edits and content syncs, and
- a `content_version` hash that **changes whenever the item's content changes**.

This pair is what makes content synchronization an idempotent upsert keyed by the stable ID:
re-syncing the same item updates it in place rather than creating a duplicate, while the
`content_version` records which revision a learner interacted with (see Section 10, failure
scenario 4.5, and `docs/API-CONVENTIONS.md` (D2), Section 4).

### 1.3 Provenance on every content item

Every content item carries provenance fields (`origin`, `model`, `prompt_id`, `prompt_version`,
`generation_date`, `reviewed_by`, `reviewed_at`) plus the `content_version` hash and `stable_id`.
Rather than repeat these columns on every catalog table, the schema factors them into a shared
`content_provenance` record that each content item references one-to-one (Section 4). This keeps
the AI-drafted / expert-approved audit trail uniform across tracks, modules, lessons, labs,
projects, quizzes, questions, and concepts.

### 1.4 Timestamps and the injected clock

Time-bearing fields (`created_at`, `updated_at`, `generation_date`, `reviewed_at`,
`first_answered_at`, `due_at`, `completed_at`, decay computations, and the 24-hour window) are all
derived from the injected `Clock`; the domain never reads the wall clock directly
(#[[file:.kiro/steering/structure.md]]). Decay is therefore deterministic from the clock
(Section 7.3).

---

## 2. Entity Groups at a Glance

| Group | Entities |
|-------|----------|
| Identity | `user`, `session`, `oauth_link`, `role_assignment`, `credential`, `verification_link`, `reset_link`, `pending_oauth_link` |
| Catalog | `track`, `module`, `lesson`, `lab`, `lab_task`, `lab_check`, `project`, `rubric`, `rubric_criterion`, `quiz`, `question`, `question_option`, `concept`, `prerequisite` |
| Provenance | `content_provenance` (shared `origin`/`model`/`prompt_id`/`prompt_version`/`generation_date`/`reviewed_by`/`reviewed_at` + `content_version` + `stable_id`) |
| Progress | `enrollment`, `module_progress` (incl. `tested_out`), `lesson_completion`, `lab_session`, `lab_run`, `lab_task_completion`, `quiz_attempt`, `answer`, `project_submission` |
| Mastery and review | `mastery` (0–100 per user × concept), `mastery_event`, `review_schedule`, `review_attempt`, `hint_reveal` |
| AI/review ops | `review_queue_item` (review columns added by `0005_ai_content`), `variant` (review columns added by `0005_ai_content`), `variant_revision` (created by `0005_ai_content`), `item_flag` (created by `0005_ai_content`), `item_observation` (created by `0005_ai_content`, personal data), `ai_cursor` (created by `0005_ai_content`), `problem_report` (created by `0005_ai_content`, personal data), `advisor_audit_log` (created by `0005_ai_content`, personal data), `generation_job` (job identity, stage, prompt and cost-cap columns added by revision `0005_ai_content`), `item_statistic`, `path_recommendation` (`stable_id`, `created_at`, `dismissed_at` and `run_id` added by `0005_ai_content`, personal data), `budget_ledger` (per-attempt columns added by `0005_ai_content`) |
| Assessments | `placement`, `final_assessment` (schema and interfaces only for now) |

---

## 3. Full Schema — Mermaid ER Diagram

The diagram below is the full schema. Attribute lists show the fields that carry identity,
mastery, review, decay, provenance, and the failure-scenario state discussed in later sections;
routine audit columns (`created_at`, `updated_at`) are implied on every table and omitted from the
diagram for readability.

```mermaid
erDiagram
    user ||--o{ session : has
    user ||--o{ oauth_link : links
    user ||--o{ role_assignment : granted
    user ||--o| credential : authenticates
    user ||--o{ verification_link : verifies
    user ||--o{ enrollment : enrolls
    user ||--o{ mastery : holds
    user ||--o{ mastery_event : generates
    user ||--o{ review_schedule : scheduled
    user ||--o{ hint_reveal : reveals
    user ||--o{ placement : takes
    user ||--o{ final_assessment : sits

    content_provenance ||--|| track : describes
    content_provenance ||--|| module : describes
    content_provenance ||--|| lesson : describes
    content_provenance ||--|| lab : describes
    content_provenance ||--|| project : describes
    content_provenance ||--|| quiz : describes
    content_provenance ||--|| question : describes
    content_provenance ||--|| concept : describes

    track ||--o{ module : contains
    module ||--o{ lesson : contains
    module ||--o{ module_progress : tracked_by
    lesson ||--o{ lesson_completion : completed_by
    lesson ||--o{ quiz : assesses
    lesson ||--o{ lab : practices

    quiz ||--o{ question : contains
    question ||--o{ question_option : offers
    question ||--o{ variant : varied_by
    question }o--o{ concept : covers

    lab ||--o{ lab_task : contains
    lab_task ||--o{ lab_check : graded_by
    lab ||--o{ lab_session : instantiated_as
    lab }o--o{ concept : covers

    project ||--|| rubric : scored_by
    rubric ||--o{ rubric_criterion : has
    project ||--o{ project_submission : submitted_as
    project }o--o{ concept : covers

    concept ||--o{ prerequisite : requires
    concept ||--o{ mastery : measured_by
    concept ||--o{ review_schedule : reviewed_on
    concept ||--o{ item_statistic : summarized_by
    user ||--o{ item_observation : answers
    user ||--o{ problem_report : files
    user ||--o{ advisor_audit_log : audited
    item_observation }o--|| item_statistic : folded_into

    enrollment ||--o{ module_progress : spans
    module_progress ||--o{ lesson_completion : includes

    lab_session ||--o{ lab_run : produces
    lab_run ||--o{ lab_task_completion : records

    quiz ||--o{ quiz_attempt : attempted_in
    quiz_attempt ||--o{ answer : contains
    question ||--o{ answer : answered_by

    mastery ||--o{ mastery_event : updated_by
    review_schedule ||--o{ review_attempt : attempted_in
    hint_reveal }o--|| question : on

    review_queue_item }o--o| variant : targets
    variant ||--o{ variant_revision : has_revisions
    generation_job ||--o{ variant : produces
    generation_job ||--o{ budget_ledger : charges
    user ||--o{ path_recommendation : recommended_for

    user {
        int id PK
        string stable_id UK
        string email
        string display_name
        string locale
        string status
        datetime deleted_at
    }
    session {
        int id PK
        int user_id FK
        string server_side_ref
        datetime expires_at
        datetime revoked_at
    }
    oauth_link {
        int id PK
        int user_id FK
        string provider
        string subject
    }
    role_assignment {
        int id PK
        int user_id FK
        string role
        string track_scope
    }
    credential {
        int id PK
        int user_id FK "unique"
        string secret "password hash"
        datetime updated_at
    }
    verification_link {
        int id PK
        string token_digest UK "SHA-256 hex"
        int user_id FK
        datetime issued_at
        datetime expires_at
        datetime consumed_at
    }

    content_provenance {
        int id PK
        string stable_id UK
        string content_version
        string origin
        string model
        string prompt_id
        string prompt_version
        datetime generation_date
        int reviewed_by FK
        datetime reviewed_at
    }

    track {
        int id PK
        string stable_id UK
        int provenance_id FK
        string title_fr
        string title_en
        string status
    }
    module {
        int id PK
        string stable_id UK
        int track_id FK
        int provenance_id FK
        int ordinal
    }
    lesson {
        int id PK
        string stable_id UK
        int module_id FK
        int provenance_id FK
        int ordinal
    }
    lab {
        int id PK
        string stable_id UK
        int lesson_id FK
        int provenance_id FK
    }
    lab_task {
        int id PK
        string stable_id UK
        int lab_id FK
        int ordinal
    }
    lab_check {
        int id PK
        string stable_id UK
        int lab_task_id FK
        string check_kind
    }
    project {
        int id PK
        string stable_id UK
        int provenance_id FK
    }
    rubric {
        int id PK
        string stable_id UK
        int project_id FK
    }
    rubric_criterion {
        int id PK
        int rubric_id FK
        int max_points
    }
    quiz {
        int id PK
        string stable_id UK
        int lesson_id FK
        int provenance_id FK
    }
    question {
        int id PK
        string stable_id UK
        int quiz_id FK
        int provenance_id FK
        string prompt_text
    }
    question_option {
        int id PK
        int question_id FK
        boolean is_correct
    }
    concept {
        int id PK
        string stable_id UK
        int provenance_id FK
        string title_fr
        string title_en
    }
    prerequisite {
        int id PK
        int concept_id FK
        int requires_concept_id FK
    }

    enrollment {
        int id PK
        int user_id FK
        int track_id FK
        datetime enrolled_at
    }
    module_progress {
        int id PK
        int enrollment_id FK
        int module_id FK
        boolean tested_out
        string state
    }
    lesson_completion {
        int id PK
        int module_progress_id FK
        int lesson_id FK
        datetime completed_at
    }
    lab_session {
        int id PK
        string stable_id UK
        int user_id FK
        int lab_id FK
        datetime started_at
        datetime ttl_at
        string status
    }
    lab_run {
        int id PK
        int lab_session_id FK
        string status
        datetime graded_at
    }
    lab_task_completion {
        int id PK
        int lab_run_id FK
        int lab_task_id FK
        boolean passed
    }
    quiz_attempt {
        int id PK
        string stable_id UK
        int user_id FK
        int quiz_id FK
        string idempotency_key UK
        int score
        boolean is_best
    }
    answer {
        int id PK
        int quiz_attempt_id FK
        int question_id FK
        datetime first_answered_at
        boolean counted_for_mastery
    }
    project_submission {
        int id PK
        string stable_id UK
        int user_id FK
        int project_id FK
        string review_outcome
    }

    mastery {
        int id PK
        int user_id FK
        int concept_id FK
        int score
        string state
    }
    mastery_event {
        int id PK
        int mastery_id FK
        int delta
        string source
        datetime occurred_at
    }
    review_schedule {
        int id PK
        int user_id FK
        int concept_id FK
        datetime due_at
        int days_overdue
    }
    review_attempt {
        int id PK
        int review_schedule_id FK
        boolean passed
        datetime attempted_at
    }
    hint_reveal {
        int id PK
        int user_id FK
        int question_id FK
        datetime revealed_at
    }

    review_queue_item {
        int id PK
        string stable_id UK
        int variant_id FK "nullable"
        string state
        int reviewer_id FK
        string kind
        string target_kind
        string target_stable_id
        string revision_content_version
        string track_stable_id
        string reason
        string comment
        datetime decided_at
        datetime created_at
    }
    variant {
        int id PK
        string stable_id UK
        int question_id FK
        int generation_job_id FK
        string lifecycle_state
        string parent_question_stable_id
        string track_stable_id
        string approved_content_version
        string withdrawn_reason
        datetime created_at
    }
    variant_revision {
        int id PK
        int variant_id FK
        string content_version
        string body_json
        string state
        string origin
        string model
        string prompt_id
        string prompt_version
        datetime generation_date
        int reviewed_by FK
        datetime reviewed_at
        string findings_json
        datetime created_at
    }
    item_flag {
        int id PK
        string item_kind
        string item_stable_id
        string reason_kind
        string detail
        datetime flagged_at
        datetime cleared_at
        int cleared_by_task FK
    }
    generation_job {
        int id PK
        string status
        string model
        int tokens
        int cost_cents
        string stable_id UK
        string kind
        string track_stable_id
        string stage
        string unit_key
        string prompt_id
        string prompt_version
        int cost_cap_cents
        string branch
        datetime started_at
        datetime finished_at
        text findings_json
    }
    item_statistic {
        int id PK
        int concept_id FK
        int variant_id FK
        float correct_rate
        float avg_time_ms
        string item_kind
        string item_stable_id
        int sample_count
        int correct_count
        int timed_count
        bigint total_time_ms
        datetime updated_at
    }
    item_observation {
        int id PK
        int user_id FK
        string item_kind
        string item_stable_id
        string variant_ref
        boolean correct
        int elapsed_ms
        datetime observed_at
    }
    ai_cursor {
        string name PK
        bigint value
    }
    problem_report {
        int id PK
        string stable_id UK
        int user_id FK
        string item_kind
        string item_stable_id
        string content_version
        string category
        text comment
        datetime created_at
    }
    path_recommendation {
        int id PK
        string stable_id UK
        int user_id FK
        string source
        string ordering
        datetime created_at
        datetime dismissed_at
        string run_id
    }
    advisor_audit_log {
        int id PK
        string stable_id UK
        int user_id FK
        string run_id
        string handle
        string source
        string outcome
        text reason
        text returned_ids_json
        datetime recorded_at
    }
    budget_ledger {
        int id PK
        int generation_job_id FK
        int tokens
        int cost_cents
        int attempt_no
        string model
        int input_tokens
        int output_tokens
        string outcome
        string track_stable_id
        datetime recorded_at
    }

    placement {
        int id PK
        int user_id FK
        int track_id FK
        string status
    }
    final_assessment {
        int id PK
        int user_id FK
        int track_id FK
        string status
    }
```

---

## 4. Identity Group

| Entity | Purpose | Personal data |
|--------|---------|---------------|
| `user` | The account: `email`, `display_name`, `locale`, `status`, `deleted_at`, plus `stable_id`. | Yes |
| `session` | Server-side session backing (`server_side_ref`, `expires_at`, `revoked_at`). The concrete backing is recorded as an ADR (session storage). | Yes |
| `oauth_link` | Links a `user` to an external identity provider (`provider`, `subject`). | Yes |
| `role_assignment` | Grants a role: learner, reviewer (with per-track expertise via `track_scope`), author, admin. | Yes |
| `credential` | One password hash per user (`secret`, `updated_at`); not API-addressable, no `stable_id`. Added by revision `0003`. | Yes |
| `verification_link` | Single-use email verification link (`token_digest` = SHA-256 of the raw token, `issued_at`, `expires_at`, `consumed_at`); no `stable_id`. Added by revision `0003`. | Yes |

Roles are exactly learner, reviewer with per-track expertise, author, and admin, matching the
identity model in #[[file:.kiro/steering/auth-privacy.md]].

---

## 5. Provenance Group

`content_provenance` is the shared provenance record referenced one-to-one by every content item
(track, module, lesson, lab, project, quiz, question, concept). It carries the provenance fields
required on every content item:

- `origin` — AI-drafted vs. human-authored.
- `model`, `prompt_id`, `prompt_version`, `generation_date` — the AI generation trail.
- `reviewed_by`, `reviewed_at` — the expert-approval trail (content is drafted by AI and approved
  by experts).
- `content_version` — the hash that changes whenever the item's content changes.
- `stable_id` — the immutable identity anchor.

The `stable_id` + `content_version` pair is the backbone of idempotent content sync (Section 10).

---

## 6. Catalog Group

| Entity | Purpose |
|--------|---------|
| `track` | Top-level learning track; bilingual `title_fr`/`title_en`. |
| `module` | Ordered unit within a track (`ordinal`). |
| `lesson` | Ordered unit within a module (`ordinal`). |
| `lab` | Hands-on lab attached to a lesson (real terminals, graded code). |
| `lab_task` | Ordered step within a lab. |
| `lab_check` | A graded check on a lab task (`check_kind`). |
| `project` | A project, scored by exactly one `rubric`. |
| `rubric` / `rubric_criterion` | Scoring rubric and its weighted criteria (`max_points`). |
| `quiz` | Quiz attached to a lesson. |
| `question` / `question_option` | Quiz question and its options (`is_correct`). |
| `concept` | A unit of knowledge mastery is measured against; bilingual titles. |
| `prerequisite` | A directed edge `concept → requires_concept` expressing prerequisites. |

Quizzes, labs, and projects each cover one or more concepts (`question }o--o{ concept`,
`lab }o--o{ concept`, `project }o--o{ concept`). A lab check contributes to reaching `Mastered`
where a lab covers the concept (#[[file:.kiro/steering/domain-rules.md]]).

---

## 7. Mastery and Review Group

### 7.1 Mastery fields

- `mastery` holds the learner's score **0–100 per user × concept** and the derived `state`.
  The states and thresholds (transcribed from the domain rules) are:
  - `Unseen` → `Introduced`: first encounter (< 30).
  - `Introduced` → `Practiced`: 30-59.
  - `Practiced` → `Proficient`: 60-84.
  - `Proficient` → `Mastered`: >= 85 + lab check (if lab covers) + passed review.
  - `Mastered` → `Proficient`: decay (never below 30 once Practiced).
  - `Proficient` → `Practiced`: decay (never below 30).
- `mastery_event` is the append-only audit of every change to a `mastery` row: the `delta`, the
  `source` (quiz answer, lab check, passed review, decay, hint reveal), and `occurred_at`. A hint
  reveal is recorded as a mastery event so hint use is reflected in the learner's progress.

### 7.2 Review fields

- A concept enters the spaced review schedule on **first reaching Practiced**. `review_schedule`
  holds the per-user × concept `due_at` and `days_overdue`.
- `review_attempt` records each attempt against a schedule (`passed`, `attempted_at`). A passed
  review is one of the conditions for reaching `Mastered`.

### 7.3 Decay fields

- Decay is **-1/day overdue, deterministic from the clock**. `review_schedule.days_overdue` is
  computed from `due_at` and the injected clock; the per-day decrement is applied to
  `mastery.score` through a `mastery_event` whose `source` is decay.
- Decay **never relocks content; it only re-queues for review.** There is no field that gates or
  locks catalog access on decay; the only effect is a re-queued `review_schedule` and a bounded
  reduction of `mastery.score`.
- Decay **never drops a score below 30 once the concept has reached Practiced** — the `mastery`
  state floor (`never below 30`) is enforced when the decay `mastery_event` is applied.

### 7.4 Hints

- `hint_reveal` records each hint a learner reveals (`user_id`, `question_id`, `revealed_at`) and
  produces a corresponding `mastery_event`, so a revealed hint is reflected in the worked mastery
  example and in the learner's progress.

---

## 8. Progress Group

| Entity | Purpose |
|--------|---------|
| `enrollment` | A learner enrolled in a track. |
| `module_progress` | Per-module progress; carries `tested_out` so a learner who proves mastery at placement can test out of a module rather than working through it step by step. |
| `lesson_completion` | Records a completed lesson under a module's progress. |
| `lab_session` | A running lab instance with `started_at`, `ttl_at`, and `status` (one sandbox per session). |
| `lab_run` | A single graded run within a lab session (`status`, `graded_at`). |
| `lab_task_completion` | Per-task pass/fail within a run. |
| `quiz_attempt` | A quiz attempt; carries the `idempotency_key`, the `score`, and `is_best`. |
| `answer` | A single answered question within an attempt; carries `first_answered_at` and `counted_for_mastery` (Section 9). |
| `project_submission` | A project submission and its `review_outcome` from the `ProjectReviewer` extension point. |

---

## 9. Failure Scenario 4.6 — Duplicate Quiz Submission (data level)

**Condition:** a duplicate quiz submission is received.

This scenario is documented at the API level in `docs/API-CONVENTIONS.md` (D2), Section 6. At the
data level the schema enforces the same normative behavior:

1. **An idempotency key dedupes the submission.** `quiz_attempt.idempotency_key` is **unique**
   (`UK`). A second submission carrying the same idempotency key for the same operation does not
   create a second `quiz_attempt` row; the stored result of the first processing is returned. The
   idempotency key is retained long enough to cover client retries (D2, Section 5).
2. **Re-answering the same question within 24 hours of the first answer yields no additional
   mastery gain.** `answer.first_answered_at` records when the learner first answered the question.
   When the same question is answered again **within 24 hours** of `first_answered_at`, the repeat
   `answer` is written with `counted_for_mastery = false`, so no additional `mastery_event` with a
   positive `delta` is produced for that re-answer. The 24-hour window is computed from the
   injected clock (Section 1.4). The domain rule is in #[[file:.kiro/steering/domain-rules.md]].
3. **The best attempt still counts for completion.** Across a learner's attempts on a quiz, the
   attempt with the highest `score` carries `is_best = true`; completion is evaluated against the
   best attempt, independent of deduplication and the 24-hour no-additional-mastery window.

---

## 10. Failure Scenario 4.5 — Content Sync During Active Use (data level)

**Condition:** content is synced while an item is in active use.

This scenario is documented in coordination with `docs/AI-CONTENT-DESIGN.md` (D6). At the data
level the schema enforces the following normative behavior:

1. **The upsert is idempotent, keyed by the stable ID.** Content sync matches on `stable_id`
   (Section 1.2). Re-syncing the same item updates the existing row in place and bumps its
   `content_version`; it never inserts a duplicate. This is the same idempotent-upsert-by-stable-ID
   rule stated in `docs/API-CONVENTIONS.md` (D2), Section 4.
2. **Edits never reset progress.** Progress rows (`module_progress`, `lesson_completion`,
   `lab_task_completion`, `quiz_attempt`, `answer`, `mastery`, `mastery_event`, `review_schedule`,
   `review_attempt`) key on the content item's `stable_id`, not its `content_version`. A bumped
   `content_version` therefore leaves every existing progress row intact.
3. **Existing completions are kept.** Rows such as `lesson_completion` and `lab_task_completion`
   already written against the item are retained unchanged when the item is re-synced.
4. **New attempts use the new version.** Attempt-bearing rows record the `content_version` in
   effect at attempt time, so attempts started after a sync are evaluated against the new version
   while prior attempts remain tied to the version they were taken under.
5. **Removed items are archived rather than deleted.** When a sync removes an item, its catalog row
   is transitioned to an archived `status` (for example `track.status`, `module`/`lesson`/`quiz`
   status via their rows) rather than being hard-deleted, so historical progress that references
   the `stable_id` stays valid.

---

## 11. AI / Review Ops Group

| Entity | Purpose |
|--------|---------|
| `review_queue_item` | A Review_Task: a variant, an edited revision or a flagged item awaiting reviewer action (`state`, `reviewer_id`). |
| `variant_revision` | One reviewable text of a variant (FR and EN prompt, options, key) keyed by `(variant_id, content_version)`. A reviewer edit of an Approved or Served variant adds a revision and leaves the variant state alone; `variant.approved_content_version` names the revision that is served. |
| `item_flag` | An active or cleared flag on a question, variant, lesson or lab, addressed by `(item_kind, item_stable_id)`. An item with an active flag is never served. |
| `variant` | A generated question variant with a `lifecycle_state` (Generated → Verified → PendingReview → Approved → Served → Withdrawn, Withdrawn → Approved on reviewer reinstatement, with Flagged/Rejected terminal states). Only approved variants are served. |
| `generation_job` | An AI generation job recording `status`, `model`, `tokens`, and `cost_cents` — recorded even when output is invalid (scenario 4.4, D6). |
| `item_statistic` | Per-item aggregate `correct_rate` and `avg_time_ms` (addressed by `(item_kind, item_stable_id)`) used to flag items with abnormal statistics. It holds the integer sums the fold keeps and no learner link. |
| `item_observation` | One answer to a question, lesson or lab, appended by the quiz and review use cases and folded into `item_statistic` by the worker. Holds the learner, so it is personal data. |
| `ai_cursor` | A named position of an AI worker job (`item_quality`: the last `item_observation` id folded). |
| `problem_report` | A learner's report on a question, lesson or lab, with the `content_version` the learner saw, a `category` and an optional free-text `comment`. Holds the learner and the comment, so it is personal data. |
| `path_recommendation` | A learner path recommendation with its `source` (LLM advisor or the rule-based fallback), the resulting `ordering`, the `run_id` of the advisor run, `created_at` and the learner's `dismissed_at`. Holds the learner, so it is personal data. |
| `advisor_audit_log` | One row per advisor run: the per-run Pseudonymous_Handle, `source`, `outcome`, the `reason` a model order was discarded and the identifiers the model returned. Holds the learner, so it is personal data. |
| `budget_ledger` | The token/cost ledger charged by each generation job. |

Revision `0005_ai_content` (additive, inspect-then-add, like `0002_labs_runtime`) adds these columns:

| Table | Added columns |
|-------|---------------|
| `generation_job` | `stable_id` (unique), `kind` (`stage`, `refill`, `advisor`, `eval`), `track_stable_id`, `stage`, `unit_key`, `prompt_id`, `prompt_version`, `cost_cap_cents`, `branch`, `started_at`, `finished_at`, `findings_json`. `status`, `model`, `tokens` and `cost_cents` stay as the job totals. |
| `budget_ledger` | `attempt_no`, `model`, `input_tokens`, `output_tokens`, `outcome` (`ok`, `invalid`, `timeout`, `unavailable`, `binding`), `track_stable_id`, `recorded_at`. `tokens` and `cost_cents` hold the attempt's totals. One row per attempt. |

| `variant` | `parent_question_stable_id`, `track_stable_id`, `approved_content_version`, `withdrawn_reason`, `created_at`. All nullable so earlier rows survive. `lifecycle_state` keeps the D6 state names. Index `ix_variant_parent_state` serves the pool-size queries. |
| `item_statistic` | `item_kind`, `item_stable_id` (unique together, both nullable so earlier rows survive), `sample_count`, `correct_count`, `timed_count` (answers that had an elapsed time), `total_time_ms` (sum of clamped elapsed times; NOT NULL, default 0) and `updated_at`. `correct_rate` and `avg_time_ms` stay as the derived columns written with each fold. Index `uq_item_statistic_item`. |
| `review_queue_item` | `stable_id` (unique, backfilled as `task-<id>` for earlier rows), `kind` (`new_variant`, `edited_revision`, `flagged_item`), `target_kind` (`variant`, `question`, `lesson`, `lab`), `target_stable_id`, `revision_content_version`, `track_stable_id`, `reason`, `comment`, `decided_at`, `created_at`. `variant_id` becomes nullable. `state` is `open` until decided, then `approved`, `rejected` or `changes_requested`. Indexes `ix_review_queue_item_page` (keyset page of open tasks per track) and `ix_review_queue_item_target`. |

The revision also creates `variant_revision` (columns in the ER diagram above; unique `(variant_id, content_version)`; `state` is `PendingReview`, `Approved` or `Rejected`; `reviewed_by` references a reviewer account like `content_provenance.reviewed_by`). `downgrade` drops the table, the indexes and the added columns, restores `review_queue_item.variant_id` as NOT NULL and deletes the tasks without a variant, which cannot exist before this revision.

The revision also creates `item_flag`: an item (`item_kind` is `question`, `variant`, `lesson` or `lab`, with `item_stable_id`; no foreign key because the kind varies) that serving must skip, with `reason_kind` (`reports` or `statistics`), `detail`, `flagged_at`, and `cleared_at` and `cleared_by_task` (references `review_queue_item.id`) once a reviewer clears it. The partial unique index `uq_item_flag_active` on `(item_kind, item_stable_id)` where `cleared_at` is null allows at most one active flag per item and any number of cleared ones. The read port `ItemFlagReader.active_flags(keys)` lets approved-only serving exclude flagged items; the write side (`flag`, `clear_flag`) is part of the `ItemQualityRepository` of the quality loop (Milestone C).

The revision also creates `item_observation`: `id` (monotonic), `user_id` (references `user`), `item_kind` and `item_stable_id` (no foreign key, like `item_flag`; a served variant is observed as `variant`), `variant_ref` (set for a variant), `correct`, `elapsed_ms` (optional, client reported, clamped to `ITEM_ELAPSED_MS_MAX` before it is stored) and `observed_at` (injected clock). It is append-only. Indexes `ix_item_observation_user` and `ix_item_observation_observed_at` serve deletion and retention. It also creates `ai_cursor` (`name` primary key, `value`). `ItemQualityRepository.apply` writes the statistics and the cursor in one transaction, so the cursor never drifts from the statistics it describes.

The revision also creates `problem_report`: `id`, `stable_id` (unique), `user_id` (references `user`), `item_kind` and `item_stable_id` (no foreign key, like `item_flag`), `content_version` (the version the learner saw), `category` (one of `PROBLEM_REPORT_CATEGORIES`), `comment` (optional, at most `PROBLEM_REPORT_COMMENT_MAX_CHARS`, never sent to an LLM) and `created_at` (injected clock). Indexes `ix_problem_report_user`, `ix_problem_report_item` (item and version, for counting distinct reporters) and `ix_problem_report_created_at` serve deletion, the threshold count and retention. `ProblemReportRepository` offers `add`, `get`, `reporters_for(item, version)` and `delete_for_user`.

The revision also adds to the existing `path_recommendation`: `stable_id` (unique, backfilled as `rec-<id>` for rows that predate the revision), `created_at`, `dismissed_at` (set once by the learner's dismissal; a repeat keeps the first value) and `run_id`; `source` stays `llm` or `rule-based` and `ordering` holds a JSON list of concept ids. It creates `advisor_audit_log`: `id`, `stable_id` (unique), `user_id` (references `user`), `run_id`, `handle` (the per-run Pseudonymous_Handle), `source`, `outcome`, `reason`, `returned_ids_json` and `recorded_at` (injected clock). Indexes `ix_path_recommendation_user_created` (latest undismissed), `ix_path_recommendation_created_at`, `ix_advisor_audit_log_user` and `ix_advisor_audit_log_recorded_at` serve reading, deletion and retention. `PathRecommendationRepository` offers `record_run` (the recommendation and its audit row in one transaction), `get`, `latest_undismissed`, `dismiss` (idempotent), `audit_entries` and `delete_for_user`.

None of these tables holds personal data beyond the reviewer reference, except `item_observation` (the learner), `problem_report` (the learner and the comment), `advisor_audit_log` and `path_recommendation` (the learner), which are registered in Section 13. Later ai-content tasks extend this revision and this section for the
tables they introduce.

The variant lifecycle and the generation gates are authoritative in
`docs/AI-CONTENT-DESIGN.md` (D6); this group captures only the persisted state.

---

## 12. Assessments Group

| Entity | Purpose |
|--------|---------|
| `placement` | A placement assessment per user × track; schema and interfaces only for now. Feeds `module_progress.tested_out`. |
| `final_assessment` | A final assessment per user × track; schema and interfaces only for now. |

Both are represented at the schema-and-interface level only; their grading behavior is deferred to
a later phase spec.

---

## 13. Personal Data: Export, Deletion, and Retention Coverage

Every table holding personal data is covered by an **export** path, a **deletion** path, and a
**retention** policy, per #[[file:.kiro/steering/auth-privacy.md]]. The table below lists each
personal-data table and confirms its coverage. Catalog, provenance, and AI-ops tables that hold no
personal data are out of scope for export/deletion/retention and are listed as "not personal data".

| Table | Personal data | Export | Deletion | Retention |
|-------|---------------|--------|----------|-----------|
| `user` | Yes (account identity) | ✓ | ✓ | ✓ |
| `session` | Yes (session backing) | ✓ | ✓ | ✓ |
| `oauth_link` | Yes (external identity) | ✓ | ✓ | ✓ |
| `role_assignment` | Yes (per-user role) | ✓ | ✓ | ✓ |
| `credential` | Yes (password hash) | ✓ (row only; `secret` is never exported) | ✓ | ✓ (kept while the account exists; deleted with the user) |
| `verification_link` | Yes (email verification link for the user) | ✓ (row only; `token_digest` is never exported) | ✓ | ✓ (link expires after 24 h; row deleted with the user) |
| `enrollment` | Yes | ✓ | ✓ | ✓ |
| `module_progress` | Yes | ✓ | ✓ | ✓ |
| `lesson_completion` | Yes | ✓ | ✓ | ✓ |
| `lab_session` | Yes | ✓ | ✓ | ✓ |
| `lab_run` | Yes | ✓ | ✓ | ✓ |
| `lab_task_completion` | Yes | ✓ | ✓ | ✓ |
| `quiz_attempt` | Yes | ✓ | ✓ | ✓ |
| `answer` | Yes | ✓ | ✓ | ✓ |
| `project_submission` | Yes | ✓ | ✓ | ✓ |
| `mastery` | Yes | ✓ | ✓ | ✓ |
| `mastery_event` | Yes | ✓ | ✓ | ✓ |
| `review_schedule` | Yes | ✓ | ✓ | ✓ |
| `review_attempt` | Yes | ✓ | ✓ | ✓ |
| `hint_reveal` | Yes | ✓ | ✓ | ✓ |
| `item_observation` | Yes (a learner's answer to an item: `user_id`, item, correctness, elapsed time) | ✓ | ✓ (deleted with the user; aggregates in `item_statistic` are kept and carry no learner link) | ✓ (`AI_RETENTION_OBSERVATION`, 90 days) |
| `problem_report` | Yes (a learner's report: `user_id`, item, version, category, free-text `comment`) | ✓ | ✓ (deleted with the user) | ✓ (`AI_RETENTION_PROBLEM_REPORT`, 365 days) |
| `advisor_audit_log` | Yes (one row per advisor run: `user_id`, the per-run `handle`, source, outcome, reason and the identifiers the model returned) | ✓ | ✓ (deleted with the user) | ✓ (`AI_RETENTION_AUDIT`, 90 days) |
| `path_recommendation` | Yes (per-user recommendation: `user_id`, `ordering`, `source`, `run_id`, `created_at` and the learner's `dismissed_at`) | ✓ (dismissals included) | ✓ (deleted with the user, dismissals with it) | ✓ (`AI_RETENTION_RECOMMENDATION`, 90 days) |
| `placement` | Yes | ✓ | ✓ | ✓ |
| `final_assessment` | Yes | ✓ | ✓ | ✓ |
| `track`, `module`, `lesson`, `lab`, `lab_task`, `lab_check`, `project`, `rubric`, `rubric_criterion`, `quiz`, `question`, `question_option`, `concept`, `prerequisite` | No (catalog) | not personal data | not personal data | not personal data |
| `content_provenance` | No (provenance; `reviewed_by` references a reviewer account, covered via `user`) | not personal data | not personal data | not personal data |
| `variant_revision` | No (AI/review ops; `reviewed_by` references a reviewer account, covered via `user`) | not personal data | not personal data | not personal data |
| `review_queue_item`, `variant`, `generation_job`, `item_statistic`, `budget_ledger`, `item_flag`, `ai_cursor` | No (AI/review ops) | not personal data | not personal data | not personal data |

Lab tables (additive columns): added columns are covered by the same export, deletion, and retention paths as their parent rows; no lab column is excluded.

- `lab_session`: `sandbox_ref`, `last_activity_at`, `last_grading_accepted_at`, `ended_at`, `provision_attempts`, `next_provision_at`, `terminal_epoch`. Exported with the session, deleted with the user, retained under the lab-session retention window.
- `lab_run`: `stable_id`, `accepted_at`, `deadline_at`, `started_at`, `results`. `accepted_at` and `deadline_at` are timestamps from the injected `Clock`. Exported with the run, deleted with the user, retained under the lab-run retention window.
- `lab_task_completion`: `counted_for_mastery`. Exported, deleted, and retained with the completion.
- `hint_reveal`: `lab_task_id` (exactly one of `lab_task_id` and `question_id` is set). Covered by the existing `hint_reveal` export, deletion, and retention.
- Catalog columns added to `lab`, `lab_task`, and `lab_check` (`kind`, `image`, `concept_stable_ids`, `archived_at`, `required`, `timeout_seconds`, `script`) are not personal data.

Note: content archival on sync (Section 10, item 5) is distinct from personal-data deletion. A
learner deletion follows the deletion path above; a sync that removes a catalog item archives it
rather than deleting it, so progress referencing its `stable_id` stays valid.

---

## 14. Summary of Normative Values

| Topic | Normative value |
|-------|-----------------|
| Mastery scale | `mastery` is 0–100 per user × concept |
| Thresholds | <30 Introduced; 30-59 Practiced; 60-84 Proficient; >=85 + lab check (if lab covers) + passed review → Mastered |
| Decay floor | Decay never drops below 30 once Practiced; decay never relocks content, only re-queues review |
| Decay rate | -1/day overdue, deterministic from the clock |
| Review entry | A concept enters the spaced review schedule on first reaching Practiced |
| Content identity | `stable_id` (immutable) + `content_version` (changes on content change); idempotent upsert keyed by `stable_id` |
| Failure 4.5 | Idempotent upsert by stable ID; edits never reset progress; existing completions kept; new attempts use new version; removed items archived not deleted |
| Failure 4.6 | Idempotency key dedupes; re-answering the same question within **24 hours** yields no additional mastery gain; best attempt still counts for completion |
| Personal data | Every personal-data table covered by export, deletion, and retention |

---

## Related Steering and Documents

- Authentication and privacy (export/deletion/retention, identity model): #[[file:.kiro/steering/auth-privacy.md]]
- Domain rules (mastery, review, decay, hints, 24-hour window): #[[file:.kiro/steering/domain-rules.md]]
- Repository structure and layering (injected clock, ports): #[[file:.kiro/steering/structure.md]]
- Architecture — `docs/ARCHITECTURE.md` (D1)
- API conventions (stable ID, idempotency, scenario 4.6) — `docs/API-CONVENTIONS.md` (D2)
- AI-content design (scenario 4.5) — `docs/AI-CONTENT-DESIGN.md` (D6)
