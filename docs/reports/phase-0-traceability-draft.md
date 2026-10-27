# Phase-0 Traceability Table (DRAFT)

> Status: DRAFT staged in Milestone 2 — built in task 4.1 and augmented in task 4.2 with the
> unmapped / multiply-mapped flagging findings (see "Flagging findings" below). The final,
> canonical copy of this table is written into `docs/reports/phase-0.md` in Milestone 4
> (task 15.1). This draft is the working artifact that the phase-0 report incorporates. It maps
> every Rule_Line from the spec request to exactly one authoritative steering file, records
> whether the Milestone 1 transcription preserved all numbers and all normative wording, and
> records the task 4.2 verdict that no Rule_Line is unmapped (Req 1.3) or multiply-mapped
> (Req 1.4).

## Method

- **Source of rule lines:** the project rules captured in the spec request, as reflected in
  `requirements.md` (Glossary + Requirement 1) and `design.md` (Overview, Architecture,
  Components and Interfaces, Data Models, Error Handling, ADRs).
- **Target steering files:** the eleven files produced in Milestone 1 under `.kiro/steering/`.
  `api-conventions.md` (S12) is deferred to Milestone 3 and does not yet exist, so no Rule_Line
  is mapped to S12 in this draft.
- **Exactly-one mapping (Req 1.1 / 1.4):** each Rule_Line is mapped to exactly one *authoritative
  home* steering file. Some rules are deliberately echoed in additional fileMatch files for
  local context (e.g. the "LLM ahead-of-time only" principle appears in `product.md`, `tech.md`,
  `content.md`, and `ai-content.md`). Those echoes are **not** independent Rule_Lines and are
  **not** counted as a second mapping; they are listed under "Deliberate cross-references" so the
  table is not misread as a multiply-mapped line. Each distinct normative rule has one and only
  one authoritative home below.
- **Faithful transcription (Req 1.2):** for each mapping, the steering-file text was compared
  against the original rule text in the spec request / requirements / design. "Preserved"
  means no number and no normative wording changed; any added prose is only an optional
  one-line `Reason:` (or an HTML-comment reason), which Req 1.2 explicitly permits.

## Steering-file index

| ID | File | Inclusion |
|----|------|-----------|
| S1 | `.kiro/steering/product.md` | always |
| S2 | `.kiro/steering/tech.md` | always |
| S3 | `.kiro/steering/structure.md` | always |
| S4 | `.kiro/steering/domain-rules.md` | fileMatch |
| S5 | `.kiro/steering/content.md` | fileMatch |
| S6 | `.kiro/steering/labs.md` | fileMatch |
| S7 | `.kiro/steering/deployment.md` | fileMatch |
| S8 | `.kiro/steering/auth-privacy.md` | fileMatch |
| S9 | `.kiro/steering/ai-content.md` | fileMatch |
| S10 | `.kiro/steering/quality.md` | always |
| S11 | `.kiro/steering/working-agreements.md` | always |
| S12 | `.kiro/steering/api-conventions.md` | fileMatch — **deferred to Milestone 3 (not yet produced)** |

## Traceability table

Legend for *Transcription*: **Preserved** = all numbers and normative wording unchanged (optional
one-line reason only).

| # | Rule_Line | Authoritative home | Transcription |
|---|-----------|--------------------|---------------|
| R1 | EDU-IT is a bilingual (French/English) re-learning platform for IT professionals who are losing hands-on skills as AI agents take over technical work. | S1 product.md | Preserved |
| R2 | Learners rebuild skills step by step and advance only by proving mastery. | S1 product.md | Preserved |
| R3 | Mastery is proven through three mechanisms: quizzes, hands-on labs (real terminals, graded code) and projects. | S1 product.md | Preserved |
| R4 | The platform is bilingual: French and English (FR/EN); all learner-facing content and the UI are offered in both languages. | S1 product.md | Preserved |
| R5 | Content is drafted by AI and approved by experts. | S1 product.md | Preserved |
| R6 | The LLM provider is used for ahead-of-time content generation only, never on a learner's request path. | S1 product.md | Preserved |
| R7 | The platform runs on Kubernetes and is portable across EKS, AKS and on-premise targets. | S1 product.md | Preserved |
| R8 | Portable across EKS, AKS and on-premise using standard Kubernetes APIs only; provider differences live in Helm values. | S7 deployment.md | Preserved |
| R9 | `apps/web`: Next.js App Router UI, `/fr` and `/en` routes via next-intl, xterm.js terminal, Monaco editor, renders MDX with an allowlist of components, forms validated with Zod. | S2 tech.md | Preserved |
| R10 | `apps/api`: FastAPI boundary — authentication and sessions, learning endpoints, serves MDX source per locale, hosts the authenticated terminal WebSocket, enqueues jobs; validates with Pydantic v2. | S2 tech.md | Preserved |
| R11 | `apps/worker`: task-queue consumer — lab provisioning and grading orchestration, AI generation jobs, variant-pool refill, spaced-review and decay computation, reaper for expired sessions. | S2 tech.md | Preserved |
| R12 | shared Python package: domain and application code reused by api and worker; framework-free domain. | S2 tech.md | Preserved |
| R13 | PostgreSQL is the system of record for learners, content, progress, mastery, reviews, variants, statistics, recommendations, assessments. | S2 tech.md | Preserved |
| R14 | Redis backs the task queue and holds ephemeral/session state; the final queue choice is an ADR. | S2 tech.md | Preserved |
| R15 | Object storage is S3 or Azure Blob, behind the `ObjectStorage` port: lab artifacts, exports. | S2 tech.md | Preserved |
| R16 | The LLM provider sits behind the `LLMProvider` port: ahead-of-time content generation only, never on a learner's request path. | S2 tech.md | Preserved |
| R17 | The sandbox sits behind the `SandboxProvider` port: Docker (dev only), Kubernetes (prod), fake (tests). | S6 labs.md | Preserved |
| R18 | The EDU-IT repository is a monorepo with the stated top-level layout (`apps/web`, `apps/api`, `apps/worker`, `lab-images/`, `content/`, `prompts/`, `evals/`, `deploy/`, `docs/`, `.kiro/specs`, `.kiro/steering`). | S3 structure.md | Preserved |
| R19 | Dependencies point inward. The domain imports no framework and receives time through an injected `Clock`. | S3 structure.md | Preserved |
| R20 | Layering: `api → application → domain ← infrastructure`. | S3 structure.md | Preserved |
| R21 | Business numbers live in `domain/config.py` (thresholds, points, intervals, timeouts). | S3 structure.md | Preserved |
| R22 | Every external system sits behind an interface with a fake. | S2 tech.md | Preserved |
| R23 | Ports defined in the domain: `SandboxProvider`, `Mailer`, `ObjectStorage`, `Clock`, `TaskQueue`, `LLMProvider`, and the `ProjectReviewer` extension point. Each has a fake. | S2 tech.md | Preserved |
| R24 | `Clock` is injected; the domain never reads the wall clock directly. | S2 tech.md | Preserved |
| R25 | `SandboxProvider` implementations: Docker (dev only), Kubernetes (prod), Fake (tests); warm-pool extension point. | S6 labs.md | Preserved |
| R26 | `Mailer`: console dev, SMTP prod; FR/EN templates. | S8 auth-privacy.md | Preserved |
| R27 | `TaskQueue`: concrete backing chosen in ADR. | S2 tech.md | Preserved |
| R28 | `LLMProvider`: structured output validated against content schemas; bounded retries; records model, tokens, cost. | S9 ai-content.md | Preserved |
| R29 | `ProjectReviewer`: extension point — mentor, peer or AI-assisted review. | S4 domain-rules.md | Preserved |
| R30 | `SandboxProvider` interface signatures (provision, exec, attach_terminal, copy_in read-only source for grading, copy_out, destroy, health). | S6 labs.md | Preserved |
| R31 | `LLMProvider` interface signature: `complete(prompt_version, input, schema, max_retries)` returning structured output; bounded retries; records model, tokens, cost. | S9 ai-content.md | Preserved |
| R32 | Mastery is `mastery` (0–100 per user × concept). | S4 domain-rules.md | Preserved |
| R33 | Mastery states/thresholds: Unseen→Introduced (< 30); Introduced→Practiced (30-59); Practiced→Proficient (60-84); Proficient→Mastered (>= 85 + lab check (if lab covers) + passed review). | S4 domain-rules.md | Preserved |
| R34 | Decay transitions: Mastered→Proficient and Proficient→Practiced never below 30 once Practiced. | S4 domain-rules.md | Preserved |
| R35 | A concept enters the spaced review schedule on first reaching Practiced. | S4 domain-rules.md | Preserved |
| R36 | Decay = -1/day overdue, deterministic from the clock. | S4 domain-rules.md | Preserved |
| R37 | Decay never relocks content; it only re-queues for review. | S4 domain-rules.md | Preserved |
| R38 | Hint reveals are recorded (`hint_reveal`). | S4 domain-rules.md | Preserved |
| R39 | Duplicate quiz submissions are deduped by an idempotency key; re-answering the same question within 24 hours of the first answer yields no additional mastery gain; the best attempt still counts for completion. | S4 domain-rules.md | Preserved |
| R40 | Labs are hands-on: real terminals, graded code. | S6 labs.md | Preserved |
| R41 | Grading is bounded: results within a 15 s target, executed at most once per 10 s per session. | S10 quality.md | Preserved |
| R42 | A lab check contributes to reaching `Mastered` where a lab covers the concept. | S4 domain-rules.md | Preserved |
| R43 | Placement is supported (`placement`, schema and interfaces only for now). | S4 domain-rules.md | Preserved |
| R44 | `module_progress` records `tested_out`. | S4 domain-rules.md | Preserved |
| R45 | `content/` holds authored learning content (MDX per locale). | S5 content.md | Preserved |
| R46 | `apps/web` renders MDX with an allowlist of components; `apps/api` serves MDX source per locale. | S5 content.md | Preserved |
| R47 | `apps/web` uses the Next.js App Router with `/fr` and `/en` routes via next-intl. | S5 content.md | Preserved |
| R48 | Catalog content entities: `track`, `module`, `lesson`, `lab`, `lab_task`, `lab_check`, `project`, `rubric`, `rubric_criterion`, `quiz`, `question`, `question_option`, `concept`, `prerequisite`. | S5 content.md | Preserved |
| R49 | A variant is served only after it is Verified (schema + independent answer + sandbox checks) and Approved by a reviewer; only approved variants are served. | S9 ai-content.md | Preserved |
| R50 | A variant is Withdrawn on abnormal correct-rate/time or when reported. | S9 ai-content.md | Preserved |
| R51 | Variant lifecycle states and transitions (Generated→Verified, Generated→Flagged, Verified→PendingReview, PendingReview→Approved, PendingReview→Rejected, Approved→Served, Served→Withdrawn). | S9 ai-content.md | Preserved |
| R52 | Provenance on every content item: `origin`, `model`, `prompt_id`, `prompt_version`, `generation_date`, `reviewed_by`, `reviewed_at`; plus `content_version` hash and `stable_id`. | S5 content.md | Preserved |
| R53 | Content sync upsert is idempotent, keyed by stable ID; edits never reset progress; existing completions are kept; new attempts use the new version; removed items are archived rather than deleted. | S5 content.md | Preserved |
| R54 | AI/review ops entities: `review_queue_item`, `variant`, `generation_job`, `item_statistic`, `path_recommendation`, `budget_ledger`. | S9 ai-content.md | Preserved |
| R55 | The path advisor falls back to rule-based ordering. | S9 ai-content.md | Preserved |
| R56 | Specify what data is allowed in LLM requests. | S9 ai-content.md | Preserved |
| R57 | LLM provider and data residency: provider behind `LLMProvider`, DPA and EU residency. | S8 auth-privacy.md | Preserved |
| R58 | Identity entities: `user`, `session`, `oauth_link`, `role_assignment` (learner, reviewer with per-track expertise, author, admin). | S8 auth-privacy.md | Preserved |
| R59 | `apps/api` is the FastAPI boundary for authentication and sessions; the learner connects over an authenticated WebSocket for the terminal. | S8 auth-privacy.md | Preserved |
| R60 | Session storage uses a server-side session backing (concrete backing recorded as an ADR). | S8 auth-privacy.md | Preserved |
| R61 | Every table holding personal data must be covered by export, deletion and retention. | S8 auth-privacy.md | Preserved |
| R62 | Learner-facing errors are translated and actionable; details stay in logs. | S10 quality.md | Preserved |
| R63 | Error format: RFC 9457 problem details (`type`, `title`, `status`, `detail`, `instance`). | S10 quality.md | Preserved |
| R64 | `platform` namespace holds the api Deployment (+ HPA + PDB), the worker Deployment (+ HPA + PDB), and the migrations pre-upgrade Job. | S7 deployment.md | Preserved |
| R65 | `labs` namespace — restricted PSA; one pod per session. | S7 deployment.md | Preserved |
| R66 | Ingress is ingress-nginx / Gateway API and routes to the api Deployment. | S7 deployment.md | Preserved |
| R67 | The api reaches PostgreSQL and Redis; the worker reaches PostgreSQL and Redis; the worker provisions, executes and destroys the per-session pool; the api attaches the terminal WebSocket to the per-session pool. | S7 deployment.md | Preserved |
| R68 | Default-deny NetworkPolicy in `labs`; default-deny in `platform` so only api and worker reach PostgreSQL and Redis; worker egress limited to the LLM provider and the Kubernetes API. | S7 deployment.md | Preserved |
| R69 | Dedicated lab node pool with taints/tolerations; configurable RuntimeClass (gVisor or Kata; plain runc dev-only with a startup warning). | S7 deployment.md | Preserved |
| R70 | Graceful shutdown drains terminal WebSockets with a reconnect message. | S7 deployment.md | Preserved |
| R71 | `deploy/` holds Helm charts and Kubernetes manifests. | S7 deployment.md | Preserved |
| R72 | ADR topics pinned as records: Versions; Translation storage; Task queue; Session storage; MDX rendering; Decay computation; LLM provider and data residency. | S2 tech.md | Preserved |
| R73 | Sandbox provisioning failure behavior: translated error, no partial session running, reaper cleans orphans, provisioning rate-limited and retried bounded by the maximum values stated in `docs/LABS-DESIGN.md`. | S6 labs.md | Preserved |
| R74 | WebSocket drop behavior: re-verify session ownership on reconnect, present a reconnect indication, drain active sockets on graceful shutdown, persist no terminal I/O. | S8 auth-privacy.md | Preserved |
| R75 | Grading check timeout behavior: grading bounded (15 s target, at most once per 10 s per session), timeout yields a translated "could not complete" feedback instead of raw output, run recorded with a failed status. | S10 quality.md | Preserved |
| R76 | LLM timeout / invalid output behavior: bounded retries by the maximum stated in `docs/AI-CONTENT-DESIGN.md`, invalid output never auto-corrected, item flagged for review, path advisor falls back to rule-based ordering, job cost and tokens still recorded. | S9 ai-content.md | Preserved |
| R77 | Content sync during active use behavior (at the rule level): idempotent upsert keyed by stable ID, edits never reset progress, existing completions kept, new attempts use the new version, removed items archived rather than deleted. | S5 content.md | Preserved |
| R78 | Duplicate quiz submission behavior: idempotency key dedupes, re-answering the same question within 24 hours yields no additional mastery gain, best attempt still counts for completion. | S4 domain-rules.md | Preserved |
| R79 | Steering is the single source of truth; design documents link steering via `#[[file:.kiro/steering/<file>.md]]` rather than copying rule content, and every such reference must resolve to an existing steering file. | S11 working-agreements.md | Preserved |
| R80 | When transcribing a rule into a steering file, preserve every number and all normative wording unchanged, adding only an optional one-line reason where the rule is not self-explanatory. | S11 working-agreements.md | Preserved |
| R81 | Every steering file carries valid front matter (parseable YAML with the required inclusion keys); conditional files include a `fileMatch` pattern; always-included files declare always-on inclusion. | S11 working-agreements.md | Preserved |
| R82 | Every architecture decision is recorded as an ADR under `docs/adr/` with context, decision and consequences sections; ADR decisions flow back into the relevant steering file in the same change. | S11 working-agreements.md | Preserved |
| R83 | The design-foundation (meta) phase produces steering files and design documents only; no application source code and no files under `apps/`, `lab-images/`, `content/`, `prompts/`, `evals/`, or `deploy/` are created, modified, or deleted; only those two deliverable categories are produced. | S11 working-agreements.md | Preserved |
| R84 | `api-conventions.md` (S12) is produced only after the design document is complete. | S11 working-agreements.md | Preserved |
| R85 | Verification before acceptance: every rule line maps to exactly one steering file (none unmapped or multiply-mapped); every Mermaid diagram renders; every ADR has context/decision/consequences; each failure scenario has documented behavior and a matching IF/THEN requirement; the no-code check confirms nothing was created under the application directories. | S11 working-agreements.md | Preserved |

## Deliberate cross-references (not second mappings)

These rules have a single authoritative home (above) but are intentionally echoed in additional
fileMatch files to give local context at the point of use. They are **not** multiply-mapped
Rule_Lines; the echoed text was likewise transcribed with all numbers and normative wording
preserved.

| Rule_Line | Authoritative home | Also echoed in (context only) |
|-----------|--------------------|-------------------------------|
| R5 Content drafted by AI and approved by experts | S1 product.md | S5 content.md, S9 ai-content.md |
| R6 / R16 LLM ahead-of-time only, never on request path | S1 product.md (principle) / S2 tech.md (port) | S5 content.md, S9 ai-content.md, S10 quality.md |
| R17 / R25 Sandbox behind `SandboxProvider` (Docker/K8s/Fake) | S6 labs.md | S2 tech.md |
| R19 Dependencies point inward; injected `Clock` | S3 structure.md | S10 quality.md |
| R22 Every external system behind an interface with a fake | S2 tech.md | S3 structure.md, S10 quality.md |
| R23 Domain ports list | S2 tech.md | S3 structure.md |
| R26 `Mailer`: console dev, SMTP prod; FR/EN templates | S8 auth-privacy.md | S2 tech.md, S5 content.md |
| R28 `LLMProvider` validates output; bounded retries; records model/tokens/cost | S9 ai-content.md | S2 tech.md, S10 quality.md |
| R41 Grading bounded (15 s target, once per 10 s/session) | S10 quality.md | S4 domain-rules.md, S6 labs.md |
| R49 / R50 Variant served only if Verified + Approved; Withdrawn on abnormal signals | S9 ai-content.md | S5 content.md, S10 quality.md |
| R52 Provenance fields on every content item | S5 content.md | S9 ai-content.md |
| R53 / R77 Content sync idempotent upsert behavior | S5 content.md | S9 ai-content.md, S10 quality.md |
| R55 Path advisor falls back to rule-based ordering | S9 ai-content.md | S10 quality.md |
| R56 Specify what data is allowed in LLM requests | S9 ai-content.md | S8 auth-privacy.md |
| R57 LLM provider and data residency (DPA, EU) | S8 auth-privacy.md | S9 ai-content.md |
| R62 Learner-facing errors translated; details in logs | S10 quality.md | S5 content.md, S8 auth-privacy.md |
| R68 Default-deny NetworkPolicy in `labs` | S7 deployment.md | S6 labs.md |
| R69 Lab node pool / RuntimeClass | S7 deployment.md | S6 labs.md |
| R70 Graceful shutdown drains terminal WebSockets | S7 deployment.md | S6 labs.md, S8 auth-privacy.md |
| R74 WebSocket drop behavior | S8 auth-privacy.md | S6 labs.md |
| R78 Duplicate quiz submission behavior | S4 domain-rules.md | S10 quality.md |

## Coverage summary

- **Rule lines enumerated:** 85 (R1–R85).
- **Mapped to exactly one authoritative steering file:** 85 / 85.
- **Unmapped Rule_Lines:** 0.
- **Multiply-mapped Rule_Lines:** 0 (deliberate echoes are catalogued as context-only
  cross-references with a single authoritative home each).
- **Rule lines mapped to S12 (`api-conventions.md`):** 0 — S12 is deferred to Milestone 3 and
  not yet produced. API-conventions material (R63 error format, R39/R78 duplicate-submission)
  currently lives in its interim home (S10 quality.md / S4 domain-rules.md) and will be mirrored
  into S12 once `docs/API-CONVENTIONS.md` (D2) exists.

## Flagging findings — unmapped and multiply-mapped Rule_Lines (task 4.2)

This section records the outcome of the two flagging checks required by task 4.2, validating the
draft mapping above against Requirements 1.3 (no unmapped/orphaned Rule_Line) and 1.4 (no
Rule_Line mapped to more than one Steering_File). The checks were re-validated independently of
the task 4.1 claims: every row R1–R85 was re-read, the enumeration was confirmed contiguous (R1
through R85, no gaps and no duplicate IDs), the "Authoritative home" column was confirmed to name
exactly one steering file per row, and the deliberate cross-references were confirmed to be
context-only echoes each resolving to a single authoritative home rather than a second mapping.

### Mapping distribution (authoritative homes)

Independent tally of how the 85 authoritative homes distribute across the eleven produced
steering files (S12 is deferred and holds none):

| Steering file | Rule_Lines (authoritative home) | Count |
|---------------|---------------------------------|-------|
| S1 product.md | R1, R2, R3, R4, R5, R6, R7 | 7 |
| S2 tech.md | R9, R10, R11, R12, R13, R14, R15, R16, R22, R23, R24, R27, R72 | 13 |
| S3 structure.md | R18, R19, R20, R21 | 4 |
| S4 domain-rules.md | R29, R32, R33, R34, R35, R36, R37, R38, R39, R42, R43, R44, R78 | 13 |
| S5 content.md | R45, R46, R47, R48, R52, R53, R77 | 7 |
| S6 labs.md | R17, R25, R30, R40, R73 | 5 |
| S7 deployment.md | R8, R64, R65, R66, R67, R68, R69, R70, R71 | 9 |
| S8 auth-privacy.md | R26, R57, R58, R59, R60, R61, R74 | 7 |
| S9 ai-content.md | R28, R31, R49, R50, R51, R54, R55, R56, R76 | 9 |
| S10 quality.md | R41, R62, R63, R75 | 4 |
| S11 working-agreements.md | R79, R80, R81, R82, R83, R84, R85 | 7 |
| S12 api-conventions.md | (none — deferred to Milestone 3) | 0 |
| **Total** | **R1–R85** | **85** |

The total of the per-file counts is 85, equal to the number of enumerated Rule_Lines, and each
Rule_Line appears in exactly one row of the tally — a cross-check that the mapping is both total
and single-valued.

### Unmapped Rule_Lines (Req 1.3)

- **Rule_Lines examined:** 85 (R1–R85).
- **Rule_Lines with no steering-file mapping:** **0.**
- **List of unmapped lines:** none.
- **Verdict:** the design foundation is **COMPLETE** with respect to Req 1.3 — no Rule_Line is
  unmapped or orphaned, so there is nothing to reject as incomplete and no unmapped line to
  indicate.

### Multiply-mapped Rule_Lines (Req 1.4)

- **Rule_Lines examined:** 85 (R1–R85).
- **Rule_Lines mapped to more than one Steering_File:** **0.**
- **List of multiply-mapped lines:** none.
- **Note on deliberate cross-references:** the "Deliberate cross-references (not second mappings)"
  table lists rules that are echoed in additional fileMatch steering files for local context at
  the point of use. Each echoed rule has a single authoritative home (verified: every row of that
  table names exactly one authoritative home), and the echoes are not independent Rule_Lines.
  They therefore do not constitute a second mapping and are not counted toward the
  multiply-mapped total.
- **Verdict:** the design foundation is **CONSISTENT** with respect to Req 1.4 — no Rule_Line is
  mapped to more than one Steering_File, so there is nothing to reject as inconsistent and no
  multiply-mapped line to indicate.

### Flagging verdict

| Check | Requirement | Count | Result |
|-------|-------------|-------|--------|
| Unmapped / orphaned Rule_Lines | 1.3 | 0 | PASS — foundation complete |
| Multiply-mapped Rule_Lines | 1.4 | 0 | PASS — foundation consistent |

Both flagging checks pass: the rule-to-steering mapping is **total** (0 unmapped) and
**consistent** (0 multiply-mapped). The design foundation is not rejected on either ground; there
are no unmapped or multiply-mapped Rule_Lines to indicate.

## Transcription verdict (Req 1.2)

Every Rule_Line above was compared against the original rule text in the spec request,
`requirements.md`, and `design.md`. In all 85 cases the transcription in Milestone 1 preserved
every number (e.g. `0–100`, `< 30`, `30-59`, `60-84`, `>= 85`, `-1/day`, `15 s`, `10 s`,
`24 hours`) and all normative wording (imperative clauses, identifiers, enumerated options,
thresholds, and names). The only additions are the optional one-line `Reason:` notes (or
HTML-comment reasons), which Requirement 1.2 explicitly permits. No number and no normative
wording was changed, dropped, or added.

**Conclusion:** the rule-to-steering mapping is total (no unmapped lines), consistent (no
multiply-mapped lines once deliberate context echoes are accounted for), and faithful (all
numbers and normative wording preserved).
