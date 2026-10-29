# Requirements Document

## Introduction

This spec builds the **learning core** of EDU-IT: the pure domain engine, the content pipeline,
and the learner-facing learning experience — **without labs** and **without AI runtime features**.
It follows the foundations phase (`docs/reports/phase-1.md`) and inherits every authoritative rule
from steering: progression and mastery (#[[file:.kiro/steering/domain-rules.md]]), content pipeline
and localization (#[[file:.kiro/steering/content.md]]), quality and testing bars
(#[[file:.kiro/steering/quality.md]]), API conventions (#[[file:.kiro/steering/api-conventions.md]]),
authentication and privacy (#[[file:.kiro/steering/auth-privacy.md]]), layering
(#[[file:.kiro/steering/structure.md]]), technology (#[[file:.kiro/steering/tech.md]]), and process
(#[[file:.kiro/steering/working-agreements.md]]). The schema is in `docs/DATA-MODEL.md` (D3); the
worked mastery example and open questions are in `docs/RULES-REVIEW.md` (D7); ADRs A2 (translation
storage), A4 (session storage), A5 (MDX rendering), and A6 (decay computation) apply.

Steering is the single source of truth. This document does not restate rule numbers as new rules;
it references steering and ties each requirement to the authoritative value. All business numbers
(thresholds, points, intervals, timeouts) live in `domain/config.py`
(#[[file:.kiro/steering/structure.md]], #[[file:.kiro/steering/quality.md]]).

The work spans three milestones:

- **Milestone A — Domain engine.** Framework-free Python, injected `Clock`, numbers in
  `domain/config.py`, tests written from the rules **before** implementation.
- **Milestone B — Content pipeline.** Pydantic content schemas (incl. provenance), `edu-it content
  validate`, idempotent `edu-it content sync`, a CI step, and expert-reviewed seed content.
- **Milestone C — Learning experience.** FastAPI learning endpoints and `apps/web` pages for the
  learner flow, GDPR export/deletion coverage, placement and final assessment as interfaces only.

**Out of scope:** labs (surfaced in the UI as "coming soon"; the domain rule engine still handles
lab-covered mastery in tests per #[[file:.kiro/steering/domain-rules.md]]) and AI runtime features
(generation, advisor, variant pipeline). Labs and AI remain as ports/interfaces with fakes.

## Glossary

- **Domain_Engine**: The framework-free domain layer (shared Python package) that computes
  unlocking, completion, quiz scoring, mastery, decay, review scheduling, and rubric evaluation. It
  reads time only through the injected `Clock` (#[[file:.kiro/steering/structure.md]]).
- **Clock**: The injected time port; the domain never reads the wall clock directly
  (#[[file:.kiro/steering/structure.md]], ADR A6).
- **Config**: `domain/config.py`, the single framework-free home for business numbers — thresholds,
  points, intervals, timeouts (#[[file:.kiro/steering/quality.md]]).
- **Learning_API**: The `apps/api` FastAPI boundary for enrollment, progress, lessons, quiz
  attempts, hints, reviews, and project submissions (#[[file:.kiro/steering/structure.md]]).
- **Web_App**: The `apps/web` Next.js App Router UI with `/fr` and `/en` routes via next-intl,
  rendering MDX through an allowlist (#[[file:.kiro/steering/content.md]], ADR A5).
- **Content_Validator**: The `edu-it content validate` command that checks authored content against
  the Pydantic content schemas.
- **Content_Sync**: The `edu-it content sync` command performing idempotent upsert keyed by
  `stable_id` (#[[file:.kiro/steering/content.md]], `docs/DATA-MODEL.md` D3 §10).
- **Content_Schema**: The Pydantic v2 schemas that are the single definition of every content
  format, including provenance, and that validate AI output in a later phase
  (#[[file:.kiro/steering/content.md]]).
- **Mastery**: The learner's per-user × concept score, 0–100, with states Unseen, Introduced,
  Practiced, Proficient, Mastered (#[[file:.kiro/steering/domain-rules.md]], `docs/DATA-MODEL.md`
  D3 §7).
- **Review_Scheduler**: The Domain_Engine component that places a concept into the spaced review
  schedule on first reaching Practiced and computes due/overdue state from the Clock.
- **Decay**: The -1/day-overdue, clock-deterministic reduction of Mastery that never relocks content
  and never drops below 30 once Practiced (#[[file:.kiro/steering/domain-rules.md]], ADR A6).
- **Rubric**: A project scoring rubric composed of weighted criteria (`docs/DATA-MODEL.md` D3 §6).
- **Golden_Set**: The approved seed content marked as the reference set for Phase 4 evals.
- **Idempotency_Key**: The client-generated `Idempotency-Key` on unsafe mutations
  (#[[file:.kiro/steering/api-conventions.md]] §5).
- **Seed_Content**: The track/module/lesson/quiz/project content authored (origin `ai`) in
  Milestone B.

## Requirements

### Requirement 1: Domain layering and injected time

**User Story:** As a platform engineer, I want the Domain_Engine to stay framework-free and
deterministic, so that every learning rule is unit-testable without external systems.

#### Acceptance Criteria

1. THE Domain_Engine SHALL import no web, database, or task-queue framework, consistent with
   `api → application → domain ← infrastructure` (#[[file:.kiro/steering/structure.md]]).
2. WHEN the Domain_Engine requires the current instant, THE Domain_Engine SHALL obtain it from the
   injected Clock rather than the wall clock (#[[file:.kiro/steering/structure.md]], ADR A6).
3. THE Domain_Engine SHALL read every threshold, point value, interval, and timeout from Config
   (`domain/config.py`) (#[[file:.kiro/steering/quality.md]]).
4. THE Domain_Engine SHALL expose the labs mechanism through the lab-check path used by the Mastered
   gate so lab-covered mastery is exercised in tests, WHILE the Learning_API and Web_App surface no
   runnable lab (#[[file:.kiro/steering/domain-rules.md]]).

### Requirement 2: Unlocking and completion

**User Story:** As a learner, I want content to unlock only as I prove mastery, so that I rebuild
skills step by step.

#### Acceptance Criteria

1. WHILE a prerequisite concept has not reached the required mastery state, THE Domain_Engine SHALL
   report the dependent module as locked (#[[file:.kiro/steering/domain-rules.md]], `docs/DATA-MODEL.md`
   D3 §6 `prerequisite`).
2. WHEN every prerequisite concept of a module reaches the required mastery state, THE Domain_Engine
   SHALL report that module as unlocked (#[[file:.kiro/steering/domain-rules.md]]).
3. WHEN a learner satisfies a lesson's completion condition, THE Domain_Engine SHALL record the
   lesson as completed via `lesson_completion` (`docs/DATA-MODEL.md` D3 §8).
4. THE Domain_Engine SHALL advance a learner only by proving mastery, never by time spent or content
   viewed (#[[file:.kiro/steering/domain-rules.md]]).
5. WHEN decay lowers a concept's Mastery, THE Domain_Engine SHALL keep previously unlocked content
   unlocked and only re-queue the concept for review (#[[file:.kiro/steering/domain-rules.md]]).

### Requirement 3: Quiz scoring, shuffling, and answer matching

**User Story:** As a learner, I want quizzes scored fairly with shuffled options, so that my result
reflects my knowledge regardless of option order.

#### Acceptance Criteria

1. WHEN a quiz attempt is scored, THE Domain_Engine SHALL compute the score as an integer 0–100 equal
   to the percentage of the quiz's questions answered correctly against each question's
   `question_option.is_correct` definition, counting any unanswered question as incorrect
   (`docs/DATA-MODEL.md` D3 §6, §9).
2. WHEN a quiz is presented with a given seed, THE Domain_Engine SHALL shuffle question options
   deterministically such that the same seed and the same option set always produce the same option
   order, and SHALL neither add, remove, nor duplicate any option
   (#[[file:.kiro/steering/domain-rules.md]]).
3. WHEN a learner's answer is evaluated after shuffling, THE Domain_Engine SHALL map the selected
   position back to its original `question_option` identity before judging correctness so shuffling
   never changes which answer is correct.
4. IF a submitted answer references an option that does not belong to its question, THEN THE
   Domain_Engine SHALL reject the attempt and persist no `quiz_attempt` or `answer` for it
   (`docs/DATA-MODEL.md` D3 §9).
5. THE Domain_Engine SHALL support quizzes containing mixed question types including a
   command-output question type, each scored against its `question_option.is_correct` definition.
6. WHEN multiple attempts exist for a quiz, THE Domain_Engine SHALL mark the highest-scoring attempt
   as `is_best`, breaking ties in favor of the most recent attempt, and SHALL evaluate completion
   against that best attempt (`docs/DATA-MODEL.md` D3 §9).

### Requirement 4: Cooldown between attempts

**User Story:** As a learner, I want a cooldown between quiz attempts, so that attempts are spaced
rather than spammed.

#### Acceptance Criteria

1. WHILE the Config cooldown interval since a learner's last attempt on a quiz has not elapsed, THE
   Domain_Engine SHALL report that quiz as in cooldown using the Clock.
2. WHEN the Config cooldown interval has elapsed since the last attempt, THE Domain_Engine SHALL
   report the quiz as attemptable again.
3. THE Domain_Engine SHALL read the cooldown interval from Config (`domain/config.py`)
   (#[[file:.kiro/steering/quality.md]]).

### Requirement 5: Mastery points, states, and thresholds

**User Story:** As a learner, I want my mastery to move through defined states, so that my progress
is visible and gated at the right thresholds.

#### Acceptance Criteria

1. THE Domain_Engine SHALL track Mastery as an integer value bounded to the inclusive range 0–100 per
   user × concept (#[[file:.kiro/steering/domain-rules.md]], `docs/DATA-MODEL.md` D3 §7).
2. WHEN a concept is first encountered AND its Mastery is below 30, THE Domain_Engine SHALL set the
   state to Introduced (#[[file:.kiro/steering/domain-rules.md]]).
3. WHEN Mastery enters the inclusive range 30–59, THE Domain_Engine SHALL set the state to Practiced
   (#[[file:.kiro/steering/domain-rules.md]]).
4. WHEN a concept reaches Practiced for the first time, THE Domain_Engine SHALL create exactly one
   spaced-review schedule entry for that concept, and SHALL NOT create an additional entry when
   Practiced is re-reached after a decay dip (#[[file:.kiro/steering/domain-rules.md]],
   `docs/RULES-REVIEW.md` D7 §2.1).
5. WHEN Mastery enters the inclusive range 60–84, THE Domain_Engine SHALL set the state to Proficient
   (#[[file:.kiro/steering/domain-rules.md]]).
6. WHEN Mastery reaches 85 or above AND a review has passed AND, where a lab covers the concept, the
   required lab check has passed, THE Domain_Engine SHALL set the state to Mastered
   (#[[file:.kiro/steering/domain-rules.md]], `docs/RULES-REVIEW.md` D7 §3.2/§6.3).
7. WHEN any change to Mastery is applied, THE Domain_Engine SHALL append a `mastery_event` recording
   the delta, source, and occurrence instant read from the injected Clock (`docs/DATA-MODEL.md`
   D3 §7.1).
8. WHEN a Mastery change would compute a value above 100, THE Domain_Engine SHALL cap the stored
   value at 100; WHEN a Mastery change would compute a value below 0, THE Domain_Engine SHALL floor
   the stored value at 0 (#[[file:.kiro/steering/domain-rules.md]], `docs/RULES-REVIEW.md` D7 §9).
9. WHILE a concept is in the Mastered state, IF decay lowers its Mastery below 85, THEN THE
   Domain_Engine SHALL set the state to Proficient without lowering Mastery below 30
   (#[[file:.kiro/steering/domain-rules.md]], `docs/RULES-REVIEW.md` D7 §3.3).
10. WHILE a concept is in the Proficient state, IF decay lowers its Mastery below 60, THEN THE
    Domain_Engine SHALL set the state to Practiced without lowering Mastery below 30
    (#[[file:.kiro/steering/domain-rules.md]], `docs/RULES-REVIEW.md` D7 §3.3).
11. IF a concept has ever reached Practiced, THEN THE Domain_Engine SHALL NOT, as a result of decay,
    set its state to Introduced or Unseen or lower its Mastery below 30
    (#[[file:.kiro/steering/domain-rules.md]], `docs/RULES-REVIEW.md` D7 §1.3/§2.3/§3.3).

### Requirement 6: The 24-hour anti-farming rule

**User Story:** As a platform owner, I want re-answers within 24 hours to earn no extra mastery, so
that learners cannot farm mastery by rapidly re-answering.

#### Acceptance Criteria

1. WHEN a learner answers the same question again at a time that is at or after that question's
   `first_answered_at` and strictly before `first_answered_at` plus 24 hours, THE Domain_Engine SHALL
   grant zero additional mastery gain for that re-answer (#[[file:.kiro/steering/domain-rules.md]],
   `docs/DATA-MODEL.md` D3 §9).
2. WHEN a re-answer falls inside the 24-hour window defined in criterion 1, THE Domain_Engine SHALL
   record the repeat `answer` with `counted_for_mastery` set to false and SHALL emit no
   `mastery_event` with a positive `delta` for that re-answer (`docs/DATA-MODEL.md` D3 §9).
3. WHILE a re-answer earns zero additional mastery, THE Domain_Engine SHALL still select the attempt
   with the highest score as the best attempt and count it for completion
   (#[[file:.kiro/steering/domain-rules.md]], `docs/DATA-MODEL.md` D3 §9).
4. THE Domain_Engine SHALL compute the 24-hour window boundary as `answer.first_answered_at` plus
   24 hours using the injected Clock, never the wall clock (`docs/DATA-MODEL.md` D3 §1.4, §9).
5. WHEN a learner answers the same question again at or after `first_answered_at` plus 24 hours, THE
   Domain_Engine SHALL evaluate that answer for mastery gain under the standard mastery rules with
   `counted_for_mastery` set to true (#[[file:.kiro/steering/domain-rules.md]], `docs/DATA-MODEL.md`
   D3 §9).

### Requirement 7: Hint reveals and hint multipliers

**User Story:** As a learner, I want hints available with a recorded cost, so that help is tracked
and reflected in my progress.

#### Acceptance Criteria

1. WHEN a learner reveals a hint, THE Domain_Engine SHALL record a `hint_reveal` row and a
   corresponding `mastery_event` (#[[file:.kiro/steering/domain-rules.md]], `docs/DATA-MODEL.md`
   D3 §7.4).
2. WHEN an attempt's mastery gain is computed AND a hint for that attempt's question was revealed
   before the attempt was submitted, THE Domain_Engine SHALL apply the Config hint multiplier so the
   gain is tempered relative to a no-hint answer (`docs/RULES-REVIEW.md` D7 §4.1, §10 item 1).
3. THE Domain_Engine SHALL read the hint multiplier from Config (`domain/config.py`)
   (#[[file:.kiro/steering/quality.md]]).
4. WHEN a hint for a question is revealed only after the attempt for that question was submitted, THE
   Domain_Engine SHALL NOT apply the hint multiplier to that already-submitted attempt's mastery gain
   (`docs/RULES-REVIEW.md` D7 §4.1).

### Requirement 8: Decay

**User Story:** As a learner, I want unused skills to decay predictably, so that the platform
re-queues stale concepts without punishing me unfairly.

#### Acceptance Criteria

1. WHEN a concept's review is overdue, THE Domain_Engine SHALL reduce Mastery by exactly 1 point for
   each complete 24-hour day the review is overdue, computed deterministically from the injected
   Clock such that the same Clock reading and the same review-schedule state always yield the same
   Mastery result (#[[file:.kiro/steering/domain-rules.md]], ADR A6).
2. WHILE a concept has reached Practiced or higher, THE Domain_Engine SHALL prevent decay from
   reducing Mastery below 30 (#[[file:.kiro/steering/domain-rules.md]]).
3. WHEN decay reduces Mastery, THE Domain_Engine SHALL re-queue the concept for review and SHALL NOT
   relock any content (#[[file:.kiro/steering/domain-rules.md]]).
4. WHEN decay runs more than once for the same Clock instant and the same review-schedule state, THE
   Domain_Engine SHALL produce an identical resulting Mastery value and identical review-schedule
   state, applying no additional Mastery reduction beyond the first computation for that instant
   (ADR A6).
5. WHEN decay reduces Mastery across a mastery-state threshold, THE Domain_Engine SHALL transition
   the concept from Mastered to Proficient when Mastery falls below 85 and from Proficient to
   Practiced when Mastery falls below 60, without transitioning below Practiced
   (#[[file:.kiro/steering/domain-rules.md]], ADR A6).

### Requirement 9: Review scheduling and review quiz selection

**User Story:** As a learner, I want concepts I have practiced to come back for spaced review, so
that my mastery stays durable.

#### Acceptance Criteria

1. WHEN a concept first reaches Practiced (mastery first crosses into the 30–59 band, i.e. mastery
   >= 30), THE Domain_Engine SHALL create exactly one `review_schedule` entry for that user ×
   concept, with `due_at` set to the current Clock time plus the initial review interval from Config
   (#[[file:.kiro/steering/domain-rules.md]], `docs/DATA-MODEL.md` D3 §7.2).
2. IF a concept re-reaches Practiced after a decay dip AND a `review_schedule` entry already exists
   for that user × concept, THEN THE Domain_Engine SHALL NOT create a second `review_schedule` entry
   AND SHALL resume the existing entry from its current interval and review history rather than
   resetting it to the initial review interval (`docs/RULES-REVIEW.md` D7 §2.1).
3. WHEN a review is due, THE Domain_Engine SHALL select review questions only from the concept's
   eligible question pool, and SHALL return an empty selection without error when the eligible pool
   contains zero questions (#[[file:.kiro/steering/domain-rules.md]]).
4. WHEN a review is passed, THE Domain_Engine SHALL set the next review interval to the passed-review
   interval from Config, which SHALL be greater than the current interval
   (#[[file:.kiro/steering/domain-rules.md]], `domain/config.py`).
5. WHEN a review is failed, THE Domain_Engine SHALL set the next review interval to the failed-review
   interval from Config, which SHALL be less than the current interval
   (#[[file:.kiro/steering/domain-rules.md]], `domain/config.py`).
6. THE Domain_Engine SHALL compute a concept's review state as due when the Clock time is at or after
   `review_schedule.due_at`, and SHALL compute overdue days as the whole number of days by which the
   Clock time exceeds `due_at` (`docs/DATA-MODEL.md` D3 §7.2, §7.3,
   #[[file:.kiro/steering/domain-rules.md]]).

### Requirement 10: Rubric threshold for projects

**User Story:** As a learner, I want projects scored against a rubric with a clear pass threshold,
so that submissions are evaluated consistently.

#### Acceptance Criteria

1. THE Domain_Engine SHALL evaluate a project submission against its Rubric's weighted criteria and
   `max_points` (`docs/DATA-MODEL.md` D3 §6).
2. WHEN the `ProjectReviewer` evaluation of a submission has completed AND the resulting rubric score
   meets or exceeds the Config pass threshold, THE Domain_Engine SHALL record the submission's
   `review_outcome` as passing and contribute its mastery (`docs/DATA-MODEL.md` D3 §8).
3. WHEN the `ProjectReviewer` evaluation of a submission has completed AND the resulting rubric score
   is below the Config pass threshold, THE Domain_Engine SHALL record the submission's
   `review_outcome` as not passing (`docs/DATA-MODEL.md` D3 §8).
4. THE Domain_Engine SHALL evaluate project review through the `ProjectReviewer` extension point
   using its fake in tests (#[[file:.kiro/steering/domain-rules.md]], #[[file:.kiro/steering/tech.md]]).
5. THE Domain_Engine SHALL NOT record any `review_outcome` for a submission until the
   `ProjectReviewer` evaluation for that submission has completed
   (#[[file:.kiro/steering/domain-rules.md]]).

### Requirement 11: Tests written from the rules before implementation

**User Story:** As a platform engineer, I want the domain tests authored from the rules before the
implementation, so that the engine is proven against the rules rather than its own code.

#### Acceptance Criteria

1. THE Domain_Engine test suite SHALL be written from the rules in
   #[[file:.kiro/steering/domain-rules.md]] before the corresponding implementation.
2. THE Domain_Engine test suite SHALL include at least one test for each rule in
   #[[file:.kiro/steering/domain-rules.md]] covering unlocking and completion, quiz scoring,
   shuffling and answer matching, cooldown, mastery points and levels, the 24-hour anti-farming
   rule, hint multipliers, decay, review scheduling and review quiz selection, and the rubric
   threshold.
3. THE Domain_Engine test suite SHALL achieve at least 90% statement coverage of the domain package.
4. THE Domain_Engine test suite SHALL use the fake Clock to drive all time-dependent behavior
   (#[[file:.kiro/steering/structure.md]], ADR A6).

### Requirement 12: Content schemas with provenance

**User Story:** As a content engineer, I want Pydantic schemas that define every content format, so
that authored content and later AI output share one validated contract.

#### Acceptance Criteria

1. THE Content_Schema SHALL define a Pydantic v2 schema for every content type: `track`, `module`,
   `lesson`, `lab`, `lab_task`, `lab_check`, `project`, `rubric`, `rubric_criterion`, `quiz`,
   `question`, `question_option`, `concept`, and `prerequisite` (#[[file:.kiro/steering/content.md]],
   `docs/DATA-MODEL.md` D3 §6).
2. THE Content_Schema SHALL require provenance on every content item: `origin`, `model`, `prompt_id`,
   `prompt_version`, `generation_date`, `reviewed_by`, `reviewed_at`, plus a `content_version` hash
   and a `stable_id` (#[[file:.kiro/steering/content.md]], #[[file:.kiro/steering/ai-content.md]]).
3. THE Content_Schema SHALL require both French and English values for every learner-facing field
   (#[[file:.kiro/steering/content.md]], ADR A2).
4. THE Content_Schema SHALL serve as the single definition of content formats that validates AI
   output in a later phase (#[[file:.kiro/steering/content.md]]).

### Requirement 13: `edu-it content validate`

**User Story:** As a content engineer, I want a validate command that rejects malformed content, so
that only well-formed content enters the pipeline.

#### Acceptance Criteria

1. WHEN `edu-it content validate` runs over authored content, THE Content_Validator SHALL validate
   each item against its Content_Schema and exit zero only when all items are valid.
2. IF a learner-facing field is missing a French or English translation, THEN THE Content_Validator
   SHALL report the missing-translation error and exit non-zero (#[[file:.kiro/steering/content.md]],
   ADR A2).
3. IF a content reference does not resolve to an existing item, THEN THE Content_Validator SHALL
   report the broken-reference error and exit non-zero.
4. IF the `prerequisite` graph contains a cycle, THEN THE Content_Validator SHALL report the
   prerequisite-cycle error and exit non-zero (`docs/DATA-MODEL.md` D3 §6).
5. IF MDX content references a component outside the allowlist, THEN THE Content_Validator SHALL
   report the unknown-component error and exit non-zero (ADR A5).
6. IF a content item with `origin` `ai` has no reviewer recorded in provenance, THEN THE
   Content_Validator SHALL report the missing-reviewer error and exit non-zero
   (#[[file:.kiro/steering/content.md]], #[[file:.kiro/steering/ai-content.md]]).

### Requirement 14: Idempotent `edu-it content sync`

**User Story:** As a content engineer, I want sync to be idempotent and progress-safe, so that
re-syncing content never duplicates items or resets learner progress.

#### Acceptance Criteria

1. WHEN `edu-it content sync` runs, THE Content_Sync SHALL upsert each item by `stable_id`, bump
   `content_version` when the item's content changed, and never insert a duplicate
   (#[[file:.kiro/steering/content.md]], `docs/DATA-MODEL.md` D3 §10).
2. WHEN `edu-it content sync` runs twice over unchanged content, THE Content_Sync SHALL make no
   changes on the second run.
3. WHEN content is edited and re-synced, THE Content_Sync SHALL preserve existing `lesson_completion`
   and other completion records, AND SHALL reset all other learner state tied to the edited item,
   including bookmarks, in-progress attempt scores, and notes (#[[file:.kiro/steering/content.md]],
   `docs/DATA-MODEL.md` D3 §10).
4. WHEN a sync removes an item, THE Content_Sync SHALL transition that item to an archived status
   rather than hard-deleting it (#[[file:.kiro/steering/content.md]], `docs/DATA-MODEL.md` D3 §10).
5. THE CI pipeline SHALL include a content step that runs `edu-it content validate`
   (#[[file:.kiro/steering/quality.md]], `docs/reports/phase-1.md` §3.6).

### Requirement 15: Seed content authoring (origin ai)

**User Story:** As a content author, I want a first track of seed content drafted, so that the
learning experience has real material in both languages.

#### Acceptance Criteria

1. THE Seed_Content SHALL include a track "Linux Fundamentals" with `origin` `ai` and bilingual
   titles (#[[file:.kiro/steering/content.md]]).
2. THE Seed_Content SHALL include a module "Files & Permissions" with lesson 1 "Reading permissions"
   (covering `ls -l`, `rwx`, octal) and lesson 2 "Changing permissions and ownership" (covering
   `chmod` symbolic and octal, `chown`, `chgrp`, setgid on directories, `umask`).
3. THE Seed_Content SHALL include, for each of lesson 1 and lesson 2, a 5-question quiz with mixed
   question types including one command-output question (Requirement 3.4).
4. THE Seed_Content SHALL include a project "Harden a shared project directory" requiring an
   idempotent setup script and a short write-up, scored by a Rubric with criteria correctness, least
   privilege, idempotency, and explanation (`docs/DATA-MODEL.md` D3 §6).
5. THE Seed_Content SHALL include a module "Processes" with one placeholder lesson whose module
   requires module "Files & Permissions" as a prerequisite (#[[file:.kiro/steering/domain-rules.md]]).
6. THE Seed_Content SHALL provide natural French and English for every learner-facing field, not a
   machine-literal translation (ADR A2).
7. WHERE a lesson surfaces a lab, THE Web_App SHALL fully disable access to that lab and present only
   a "coming soon" placeholder in its place, WHILE the Domain_Engine still models lab-covered mastery
   in tests (Requirement 1.4).

### Requirement 16: Expert review checkpoint and golden set

**User Story:** As a reviewer, I want to approve the seed content before it is served, so that only
expert-approved content becomes the reference set.

#### Acceptance Criteria

1. WHEN the Seed_Content is authored, THE workflow SHALL stop and ask the user to review the
   Seed_Content before any further content step proceeds (#[[file:.kiro/steering/working-agreements.md]]).
2. WHEN the user approves the Seed_Content, THE Content_Sync SHALL record that user as the reviewer
   in provenance (`reviewed_by`, `reviewed_at`) (#[[file:.kiro/steering/content.md]],
   `docs/DATA-MODEL.md` D3 §5).
3. WHEN the Seed_Content is approved, THE workflow SHALL mark the content as the Golden_Set for
   Phase 4 evals.
4. IF the Seed_Content is not approved, THEN THE workflow SHALL NOT mark any Golden_Set, SHALL NOT
   record a reviewer in provenance, AND SHALL NOT sync or serve the rejected content in any form
   (#[[file:.kiro/steering/content.md]], #[[file:.kiro/steering/working-agreements.md]]).

### Requirement 17: Enrollment, progress, and lesson endpoints

**User Story:** As a learner, I want endpoints to enroll, see my progress, and read lessons, so that
I can work through a track.

#### Acceptance Criteria

1. THE Learning_API SHALL expose an enrollment endpoint that enrolls a learner in a track and records
   an `enrollment` (`docs/DATA-MODEL.md` D3 §8).
2. THE Learning_API SHALL expose a progress endpoint returning the learner's module lock states,
   lesson completions, and per-concept mastery (`docs/DATA-MODEL.md` D3 §7, §8).
3. WHEN a lesson is requested in a locale for which no MDX translation exists, THE Learning_API SHALL
   serve the lesson in the default locale (English) rather than returning an error
   (#[[file:.kiro/steering/content.md]], ADR A5, ADR A2).
4. THE Learning_API SHALL address every resource by its `stable_id` and never expose internal numeric
   keys (#[[file:.kiro/steering/api-conventions.md]] §4, `docs/DATA-MODEL.md` D3 §1).
5. THE Learning_API SHALL paginate list endpoints with cursor-based keyset pagination over a stable
   sort key tie-broken by `stable_id` (#[[file:.kiro/steering/api-conventions.md]] §2).

### Requirement 18: Quiz attempt, hint, and review endpoints

**User Story:** As a learner, I want endpoints to attempt quizzes, reveal hints, and complete
reviews, so that I can prove and refresh mastery.

#### Acceptance Criteria

1. THE Learning_API SHALL expose a quiz-attempt submission endpoint that accepts an `Idempotency-Key`
   and records a `quiz_attempt` (#[[file:.kiro/steering/api-conventions.md]] §5).
2. WHEN a quiz-attempt submission carries the same `Idempotency-Key` for the same operation as a
   prior submission, THE Learning_API SHALL return the stored result and SHALL NOT create a second
   `quiz_attempt` (#[[file:.kiro/steering/api-conventions.md]] §6, `docs/DATA-MODEL.md` D3 §9).
3. THE Learning_API SHALL expose a hint endpoint that records a `hint_reveal` and returns the next
   progressive hint (Requirement 7).
4. THE Learning_API SHALL expose a review endpoint that returns the due review and accepts a review
   result (Requirement 9).
5. THE Learning_API SHALL expose a project-submission endpoint recording a `project_submission` with
   its rubric self-assessment and required evidence (Requirement 20, `docs/DATA-MODEL.md` D3 §8).
6. WHEN any Learning_API request fails, THE Learning_API SHALL return an RFC 9457 problem-details
   response whose learner-facing `detail` is translated and actionable, with diagnostics kept in
   logs (#[[file:.kiro/steering/api-conventions.md]] §1, #[[file:.kiro/steering/auth-privacy.md]]).

### Requirement 19: Learner pages

**User Story:** As a learner, I want pages for my dashboard, track, module, lesson, quiz, review, and
project, so that I can navigate the full learning flow in either language.

#### Acceptance Criteria

1. THE Web_App SHALL render a dashboard page showing progress and the concepts due and overdue for
   review (#[[file:.kiro/steering/domain-rules.md]], `docs/DATA-MODEL.md` D3 §7).
2. THE Web_App SHALL render a track page and a module page that shows each module's lock state
   (Requirement 2).
3. THE Web_App SHALL render a lesson reader that renders MDX through the component allowlist (ADR A5).
4. THE Web_App SHALL render a quiz player presenting feedback and progressive hints (Requirement 7).
5. THE Web_App SHALL render a review session page and a project submission page with rubric
   self-assessment and required evidence (Requirement 10, Requirement 20).
6. THE Web_App SHALL render every learner page under both `/fr` and `/en` via next-intl
   (#[[file:.kiro/steering/content.md]]).
7. WHERE a lab is referenced on a page, THE Web_App SHALL replace the lab content entirely with a
   "coming soon" placeholder AND SHALL expose no lab details, preview, or link (Requirement 15.7).

### Requirement 20: Project submission with rubric self-assessment and evidence

**User Story:** As a learner, I want to submit a project with a rubric self-assessment and required
evidence, so that my submission is complete and reviewable.

#### Acceptance Criteria

1. IF a project submission is missing a rubric self-assessment OR its self-assessment does not cover
   every Rubric criterion, THEN THE Learning_API SHALL reject the submission with an RFC 9457
   problem-details response and record no `project_submission` (Requirement 10,
   `docs/DATA-MODEL.md` D3 §6).
2. IF required evidence is absent from a project submission, THEN THE Learning_API SHALL reject the
   submission with an RFC 9457 problem-details response and record no `project_submission`
   (#[[file:.kiro/steering/api-conventions.md]] §1).
3. WHEN a complete project submission is accepted, THE Learning_API SHALL record a
   `project_submission` and route it through the `ProjectReviewer` extension point
   (#[[file:.kiro/steering/domain-rules.md]]).
4. WHILE a project submission has been started but has not yet been accepted or rejected, THE
   Learning_API SHALL expose a distinct pending/incomplete status for that submission to the learner
   (`docs/DATA-MODEL.md` D3 §8).

### Requirement 21: Placement and final assessment as interfaces only

**User Story:** As a platform engineer, I want placement and final assessment present as interfaces
and TODOs only, so that later phases can implement them without this phase committing behavior.

#### Acceptance Criteria

1. THE Learning_API SHALL define the placement and final-assessment interfaces at the schema and
   interface level only, backed by `placement` and `final_assessment` records (`docs/DATA-MODEL.md`
   D3 §12).
2. THE Learning_API SHALL mark the placement and final-assessment grading behavior as a TODO and
   SHALL implement no grading in this phase.
3. WHERE a learner tests out of a module at placement, THE Domain_Engine SHALL record `tested_out` on
   `module_progress` (#[[file:.kiro/steering/domain-rules.md]], `docs/DATA-MODEL.md` D3 §8).

### Requirement 22: GDPR export and deletion coverage

**User Story:** As a learner exercising my data rights, I want export and deletion to cover all my
learning data, so that my record is complete and can be removed.

#### Acceptance Criteria

1. WHEN a learner's data is exported, THE Learning_API SHALL include quiz attempts, mastery, reviews,
   and project submissions (#[[file:.kiro/steering/auth-privacy.md]], `docs/DATA-MODEL.md` D3 §13).
2. WHEN a learner's data is deleted, THE Learning_API SHALL delete quiz attempts, mastery, reviews,
   and project submissions (#[[file:.kiro/steering/auth-privacy.md]], `docs/DATA-MODEL.md` D3 §13).
3. THE Learning_API SHALL cover every personal-data table produced by this phase under export,
   deletion, and retention (#[[file:.kiro/steering/auth-privacy.md]], `docs/DATA-MODEL.md` D3 §13).

### Requirement 23: Failure scenario — duplicate quiz submission

**User Story:** As a learner, I want a double or concurrent submission to count once, so that a retry
or a second tab does not create duplicate attempts.

#### Acceptance Criteria

1. IF a quiz is submitted twice with the same `Idempotency-Key` for the same operation, THEN THE
   Learning_API SHALL return the stored result of the first processing and record the attempt once
   (#[[file:.kiro/steering/api-conventions.md]] §5, §6, `docs/DATA-MODEL.md` D3 §9).
2. IF two concurrent submissions carry the same `Idempotency-Key`, THEN THE Learning_API SHALL
   process the operation at most once and return the same stored result to both
   (#[[file:.kiro/steering/api-conventions.md]] §5).
3. IF a reused `Idempotency-Key` arrives with a conflicting request body, THEN THE Learning_API SHALL
   reject the request with an RFC 9457 `409 Conflict` problem-details response
   (#[[file:.kiro/steering/api-conventions.md]] §5, §6).

### Requirement 24: Failure scenario — submission during cooldown

**User Story:** As a learner, I want a clear response when I submit during cooldown, so that I know
when I can try again.

#### Acceptance Criteria

1. IF a learner submits a quiz attempt while that quiz is in cooldown, THEN THE Learning_API SHALL
   reject the submission with an RFC 9457 problem-details response and record no new attempt
   (Requirement 4, #[[file:.kiro/steering/api-conventions.md]] §1).
2. IF a submission is rejected for cooldown, THEN THE Learning_API SHALL include in the translated
   `detail` when the quiz becomes attemptable again (#[[file:.kiro/steering/auth-privacy.md]]).

### Requirement 25: Failure scenario — content changes mid-attempt

**User Story:** As a learner, I want an in-progress attempt to stay consistent when content changes,
so that a mid-attempt edit does not corrupt my attempt.

#### Acceptance Criteria

1. IF a lesson or quiz changes while a learner is mid-attempt, THEN THE Learning_API SHALL keep the
   active attempt pinned to the `content_version` it started under (`docs/DATA-MODEL.md` D3 §10).
2. WHEN a new attempt starts after a content change, THE Learning_API SHALL evaluate it against the
   new `content_version` (`docs/DATA-MODEL.md` D3 §10).
3. IF content changes mid-attempt, THEN THE Learning_API SHALL leave the learner's existing progress
   rows intact (#[[file:.kiro/steering/content.md]], `docs/DATA-MODEL.md` D3 §10).

### Requirement 26: Failure scenario — content sync during active use

**User Story:** As a platform operator, I want sync to be safe while learners are active, so that a
sync never disrupts in-flight learning.

#### Acceptance Criteria

1. IF content is synced while learners are active, THEN THE Content_Sync SHALL perform an idempotent
   upsert keyed by `stable_id`, keep existing completions, and leave progress unchanged
   (#[[file:.kiro/steering/content.md]], #[[file:.kiro/steering/ai-content.md]], `docs/DATA-MODEL.md`
   D3 §10).
2. IF a sync removes an item while learners are active, THEN THE Content_Sync SHALL defer archiving
   that item until the active sessions referencing its `stable_id` complete, so in-flight learners
   keep valid references, AND SHALL never hard-delete it (`docs/DATA-MODEL.md` D3 §10).
3. WHEN a sync bumps `content_version` during active use, THE Content_Sync SHALL leave in-flight
   attempts pinned to the version they started under (Requirement 25.1).

### Requirement 27: Failure scenario — content validation fails in CI

**User Story:** As a platform engineer, I want CI to fail on invalid content, so that malformed
content never merges.

#### Acceptance Criteria

1. IF `edu-it content validate` reports any error in CI, THEN THE CI pipeline SHALL fail non-zero and
   name the failing content step (#[[file:.kiro/steering/quality.md]], `docs/reports/phase-1.md`
   §3.6).
2. WHEN all content is valid, THE CI content step SHALL exit zero (#[[file:.kiro/steering/quality.md]]).

### Requirement 28: Failure scenario — review with too few eligible questions

**User Story:** As a learner, I want a graceful response when a concept has too few review
questions, so that reviews never present a broken or too-small pool.

#### Acceptance Criteria

1. IF a review is requested when a concept's eligible question pool has fewer than 3 questions, THEN
   THE Learning_API SHALL decline to start the review and return an RFC 9457 problem-details response
   (#[[file:.kiro/steering/api-conventions.md]] §1).
2. IF the eligible pool is below the minimum, THEN THE Domain_Engine SHALL not create or advance a
   review schedule entry for that request (Requirement 9).
3. THE Domain_Engine SHALL read the minimum eligible-question count from Config (`domain/config.py`),
   with a value of 3 (#[[file:.kiro/steering/quality.md]]).

### Requirement 29: Performance target for learning endpoints

**User Story:** As a learner, I want the learning endpoints to respond quickly, so that the
experience feels responsive.

#### Acceptance Criteria

1. WHILE serving the Seed_Content locally, THE Learning_API SHALL keep p95 latency under 300 ms for
   the enrollment, progress, lesson, quiz-attempt, hint, review, and project-submission endpoints.
2. THE performance measurement SHALL be run against the Seed_Content and recorded in
   `docs/reports/phase-2.md` (Requirement 31).

### Requirement 30: Verification checks

**User Story:** As a reviewer, I want the acceptance criteria exercised by automated checks, so that
the phase is verifiable rather than asserted.

#### Acceptance Criteria

1. THE verification suite SHALL confirm that every rule in #[[file:.kiro/steering/domain-rules.md]]
   maps to at least one EARS requirement and at least one test, and that domain coverage is at least
   90% (Requirement 11).
2. THE verification suite SHALL confirm `edu-it content validate` fails on a missing translation, a
   broken reference, a prerequisite cycle, an unknown MDX component, and an AI item without a
   reviewer (Requirement 13).
3. THE verification suite SHALL confirm that running `edu-it content sync` twice makes no changes the
   second time and that content changes never reset progress (Requirement 14).
4. THE verification suite SHALL confirm a learner can complete both lessons in either language and
   that locks lift as specified (Requirement 2, Requirement 15, Requirement 19).
5. THE verification suite SHALL confirm shuffling, answer matching, cooldown, anti-farming, and hint
   multipliers behave as specified (Requirements 3, 4, 6, 7).
6. THE verification suite SHALL, using the test Clock, show end-to-end a concept becoming due, a
   review passing, and a review failing with the expected interval changes (Requirement 9).
7. THE verification suite SHALL confirm a duplicate quiz submission is counted once (Requirement 23).
8. THE verification suite SHALL confirm export and deletion include all learning data
   (Requirement 22).
9. THE verification suite SHALL confirm Playwright with axe-core passes on the dashboard, track,
   module, lesson reader, quiz player, review session, and project submission pages (Requirement 19).
10. THE verification suite SHALL confirm the Seed_Content is approved by the user and marked as the
    Golden_Set (Requirement 16).

### Requirement 31: Phase-2 report

**User Story:** As a reviewer, I want a phase-2 report, so that the verified outcomes and commands
are recorded as the acceptance artifact.

#### Acceptance Criteria

1. WHEN the phase's verification checks have been run, THE final task SHALL write
   `docs/reports/phase-2.md` recording, for each check in Requirement 30, the command invoked and its
   recorded pass-or-fail outcome (`docs/reports/phase-1.md`).
2. WHEN a verification check fails, THE phase-2 report SHALL record the failing outcome rather than
   omit it (`docs/reports/phase-1.md` §5).
3. THE phase-2 report SHALL record the p95 latency measurement for the learning endpoints against the
   Seed_Content (Requirement 29).
