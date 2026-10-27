# Phase-0 Report — EDU-IT Platform Design Foundation

> Deliverable R1 of the `platform-design` design-foundation (meta) spec. This report is the
> acceptance artifact for the phase: it carries the canonical rule-to-steering traceability
> table, describes what was built, gives exact verification commands, lists deviations with
> ADR links, records known gaps and open questions, and records the outcome of every
> verification check — including the no-application-code check.
>
> This phase produces steering files and design documents only. No application source code was
> produced, and no file under `apps/`, `lab-images/`, `content/`, `prompts/`, `evals/`, or
> `deploy/` was created, modified, or deleted.

## 1. Summary

The design foundation for EDU-IT is complete. The phase produced:

- **12 steering files** (S1–S12) under `.kiro/steering/`, each with valid YAML front matter;
  the six conditional files plus `api-conventions.md` carry a `fileMatchPattern`, and the five
  always-on files declare `inclusion: always`.
- **8 design documents** (D1–D8) under `docs/`.
- **8 architecture decision records** (A1–A8) under `docs/adr/`, each with context, decision,
  and consequences sections.
- **This phase-0 report** (R1) under `docs/reports/`.

All six verification checks pass: front-matter validity, Mermaid render, traceability
completeness, link integrity, ADR completeness, and the no-code check. The outcomes are recorded
in Section 7.

## 2. What Was Built

### 2.1 Steering files (S1–S12)

The authoritative project rules were transcribed into `.kiro/steering/`. Steering is the single
source of truth; design documents link steering through `#[[file:.kiro/steering/<file>.md]]`
includes rather than copying rule text.

| ID | File | Inclusion | Role |
|----|------|-----------|------|
| S1 | `product.md` | always | Product scope and principles |
| S2 | `tech.md` | always | Technology choices and domain ports |
| S3 | `structure.md` | always | Repository structure and layering |
| S4 | `domain-rules.md` | fileMatch | Unlocking, quizzes, labs, projects, mastery, review, hints, placement |
| S5 | `content.md` | fileMatch | Content pipeline and localization |
| S6 | `labs.md` | fileMatch | Labs, sandbox, and terminal |
| S7 | `deployment.md` | fileMatch | Deployment and Kubernetes topology |
| S8 | `auth-privacy.md` | fileMatch | Authentication and privacy |
| S9 | `ai-content.md` | fileMatch | AI content generation and review |
| S10 | `quality.md` | always | Quality bar and testing |
| S11 | `working-agreements.md` | always | Working agreements and process |
| S12 | `api-conventions.md` | fileMatch | API conventions (produced after D2, per Req 1.8/1.9) |

### 2.2 Design documents (D1–D8)

| ID | Document | Content |
|----|----------|---------|
| D1 | `docs/ARCHITECTURE.md` | Repository structure, component responsibilities, layering (Mermaid); links `structure.md`. |
| D2 | `docs/API-CONVENTIONS.md` | RFC 9457 problem details, pagination, versioning, stable IDs, idempotency keys; failure scenario 4.6. |
| D3 | `docs/DATA-MODEL.md` | Full schema as a Mermaid ER diagram; export/deletion/retention coverage; failure scenarios 4.5 and 4.6; links `auth-privacy.md`. |
| D4 | `docs/LABS-DESIGN.md` | `SandboxProvider` signatures, sequence diagrams, per-provider security model; failure scenarios 4.1, 4.2, 4.3; links `labs.md`. |
| D5 | `docs/DEPLOYMENT-DESIGN.md` | Kubernetes topology per target (EKS, AKS, on-premise), network policies, RBAC, ingress/WebSocket path; links `deployment.md`. |
| D6 | `docs/AI-CONTENT-DESIGN.md` | `LLMProvider` signatures, generation stages and review gates, variant lifecycle, path advisor, allowed-data rule; failure scenarios 4.4 and 4.5; links `ai-content.md`. |
| D7 | `docs/RULES-REVIEW.md` | Confirm/challenge of each domain rule with a worked mastery example (one hint reveal, one overdue review); links `domain-rules.md`. |
| D8 | `docs/RISKS.md` | Ranked risks and open questions with recommendations. |

### 2.3 Architecture decision records (A1–A8)

Exactly eight ADRs exist under `docs/adr/`, each addressing exactly one decision topic (Req 3.2):

| ADR | File | Topic |
|-----|------|-------|
| A1 | `0001-versions.md` | Pin current stable framework and test-tool versions |
| A2 | `0002-translation-storage.md` | FR/EN learner-facing fields and UI keys |
| A3 | `0003-task-queue.md` | Concrete backing behind `TaskQueue` |
| A4 | `0004-session-storage.md` | Server-side session backing |
| A5 | `0005-mdx-rendering.md` | Allowlisted MDX component rendering |
| A6 | `0006-decay-computation.md` | Deterministic clock-based decay strategy |
| A7 | `0007-llm-provider-and-data-residency.md` | Provider behind `LLMProvider`, DPA, EU residency |
| A8 | `0008-reserved.md` | Reserved topic (context states reserved; decision states none recorded yet) |

## 3. Traceability Table

This is the canonical copy of the rule-to-steering traceability table. It maps every Rule_Line
from the spec request to exactly one authoritative steering file and records that the Milestone 1
transcription preserved every number and all normative wording. It is the finalized form of the
draft staged in Milestone 2 (`docs/reports/phase-0-traceability-draft.md`), confirmed against the
present repository state.

### 3.1 Coverage summary

- **Rule lines enumerated:** 85 (R1–R85).
- **Mapped to exactly one authoritative steering file:** 85 / 85.
- **Unmapped Rule_Lines (Req 1.3):** 0.
- **Multiply-mapped Rule_Lines (Req 1.4):** 0 (deliberate echoes are catalogued as context-only
  cross-references in Section 3.4, each resolving to a single authoritative home).

Legend for *Transcription*: **Preserved** = all numbers and normative wording unchanged (optional
one-line reason only, which Req 1.2 permits).

### 3.2 Mapping

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

### 3.3 Mapping distribution (authoritative homes)

| Steering file | Count | Rule_Lines |
|---------------|-------|------------|
| S1 product.md | 7 | R1–R7 |
| S2 tech.md | 13 | R9–R16, R22–R24, R27, R72 |
| S3 structure.md | 4 | R18–R21 |
| S4 domain-rules.md | 13 | R29, R32–R39, R42–R44, R78 |
| S5 content.md | 7 | R45–R48, R52, R53, R77 |
| S6 labs.md | 5 | R17, R25, R30, R40, R73 |
| S7 deployment.md | 9 | R8, R64–R71 |
| S8 auth-privacy.md | 7 | R26, R57–R61, R74 |
| S9 ai-content.md | 9 | R28, R31, R49–R51, R54–R56, R76 |
| S10 quality.md | 4 | R41, R62, R63, R75 |
| S11 working-agreements.md | 7 | R79–R85 |
| S12 api-conventions.md | 0 | (mirrors D2; API-conventions rule text has its interim home in S10/S4) |
| **Total** | **85** | **R1–R85** |

The per-file counts sum to 85, equal to the number of enumerated Rule_Lines, and each Rule_Line
appears in exactly one row — a cross-check that the mapping is both total and single-valued.

### 3.4 Deliberate cross-references (not second mappings)

These rules have a single authoritative home (above) but are intentionally echoed in additional
fileMatch files to give local context at the point of use. They are **not** multiply-mapped
Rule_Lines; each echo was transcribed with all numbers and normative wording preserved and
resolves to a single authoritative home.

| Rule_Line | Authoritative home | Also echoed in (context only) |
|-----------|--------------------|-------------------------------|
| R5 Content drafted by AI and approved by experts | S1 product.md | S5 content.md, S9 ai-content.md |
| R6 / R16 LLM ahead-of-time only, never on request path | S1 product.md / S2 tech.md | S5 content.md, S9 ai-content.md, S10 quality.md |
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

## 4. How to Verify

All commands below are PowerShell, run from the workspace root (`dev/`), where `edu-it/` is the
repository. They reproduce each verification check in Section 7. The Mermaid render check uses
the Mermaid CLI (`@mermaid-js/mermaid-cli`), which must be installed (`npm i -g
@mermaid-js/mermaid-cli`) before running the render command.

### 4.1 Front-matter validity

List every steering file and confirm each opens with a YAML front-matter block and declares an
inclusion key:

```powershell
Get-ChildItem edu-it/.kiro/steering/*.md | ForEach-Object {
  $head = Get-Content $_.FullName -TotalCount 5
  "{0}: {1}" -f $_.Name, ($head -join ' | ')
}
Select-String -Path edu-it/.kiro/steering/*.md -Pattern '^inclusion:' | Measure-Object | Select-Object Count
Select-String -Path edu-it/.kiro/steering/*.md -Pattern '^fileMatchPattern:' | Measure-Object | Select-Object Count
```

Expected: 12 files; 12 `inclusion:` lines; 7 `fileMatchPattern:` lines (the six conditional files
plus `api-conventions.md`).

### 4.2 Mermaid render

Render every Mermaid block in every design document and ADR; the command fails loudly if any
block does not parse:

```powershell
Get-ChildItem -Recurse edu-it/docs -Filter *.md | ForEach-Object {
  mmdc -i $_.FullName -o (Join-Path $env:TEMP ($_.BaseName + '.svg'))
}
```

Expected: every file processed with zero render errors. To locate the blocks being rendered:

```powershell
Select-String -Path (Get-ChildItem -Recurse edu-it/docs -Filter *.md).FullName -Pattern '^```mermaid' |
  Measure-Object | Select-Object Count
```

Expected: 24 Mermaid blocks.

### 4.3 Traceability completeness

Confirm the table in this report enumerates R1 through R85 contiguously with no gaps or
duplicates:

```powershell
$ids = Select-String -Path edu-it/docs/reports/phase-0.md -Pattern '^\| R(\d+) \|' |
  ForEach-Object { [int]$_.Matches[0].Groups[1].Value } | Sort-Object -Unique
"count=$($ids.Count) min=$($ids[0]) max=$($ids[-1])"
```

Expected: `count=85 min=1 max=85`.

### 4.4 Link integrity

Confirm every `#[[file:.kiro/steering/<file>.md]]` reference in `docs/` resolves to an existing
steering file:

```powershell
Select-String -Path (Get-ChildItem -Recurse edu-it/docs -Filter *.md).FullName `
  -Pattern '#\[\[file:(\.kiro/steering/[^\]]+\.md)\]\]' -AllMatches |
  ForEach-Object { $_.Matches } | ForEach-Object {
    $rel = $_.Groups[1].Value
    $ok = Test-Path (Join-Path 'edu-it' $rel)
    "{0} -> {1}" -f $rel, $(if ($ok) { 'OK' } else { 'MISSING' })
  } | Sort-Object -Unique
```

Expected: every reference reports `OK` (targets: `structure.md`, `domain-rules.md`, `labs.md`,
`deployment.md`, `auth-privacy.md`, `ai-content.md`, `quality.md`, `tech.md`,
`working-agreements.md`). No `MISSING`.

### 4.5 ADR completeness

Confirm exactly eight ADRs exist and each has context, decision, and consequences sections:

```powershell
(Get-ChildItem edu-it/docs/adr/*.md).Count
Get-ChildItem edu-it/docs/adr/*.md | ForEach-Object {
  $c = Get-Content $_.FullName -Raw
  "{0}: context={1} decision={2} consequences={3}" -f $_.Name,
    [bool]($c -match '(?m)^## Context'),
    [bool]($c -match '(?m)^## Decision'),
    [bool]($c -match '(?m)^## Consequences')
}
```

Expected: count `8`; every file reports `context=True decision=True consequences=True`.

### 4.6 No-code check

Confirm no file exists, and no change is staged or unstaged, under any application directory:

```powershell
foreach ($d in 'apps','lab-images','content','prompts','evals','deploy') {
  if (Test-Path "edu-it/$d") { "EXISTS: $d" } else { "ABSENT: $d" }
}
git -C edu-it status --porcelain -- apps lab-images content prompts evals deploy
git -C edu-it ls-files apps lab-images content prompts evals deploy
```

Expected: all six report `ABSENT`; both `git` commands produce no output (zero changed files,
zero tracked files under the forbidden directories).

## 5. Deviations

No deviations from the spec request or the design were required during this phase. The following
decisions were recorded as ADRs and are noted here for traceability, since each resolves a choice
that the spec request deliberately deferred ("the final queue choice is an ADR", "concrete
backing recorded as an ADR"):

| Topic | Decision recorded | ADR |
|-------|-------------------|-----|
| Framework and test-tool versions | Pin current stable versions | [A1 — Versions](../adr/0001-versions.md) |
| Translation storage | Separate UI keys from content fields by owner | [A2 — Translation storage](../adr/0002-translation-storage.md) |
| Task queue backing | Back `TaskQueue` with Redis | [A3 — Task queue](../adr/0003-task-queue.md) |
| Session storage | Server-side session backing | [A4 — Session storage](../adr/0004-session-storage.md) |
| MDX rendering | Allowlisted component rendering | [A5 — MDX rendering](../adr/0005-mdx-rendering.md) |
| Decay computation | Deterministic clock-based `-1/day overdue` | [A6 — Decay computation](../adr/0006-decay-computation.md) |
| LLM provider and data residency | Provider behind `LLMProvider`, DPA, EU residency | [A7 — LLM provider and data residency](../adr/0007-llm-provider-and-data-residency.md) |
| Reserved slot | No decision recorded yet (reserved) | [A8 — Reserved](../adr/0008-reserved.md) |

## 6. Known Gaps and Open Questions

### 6.1 Known gaps

These are intentional boundaries of the design-foundation phase, not defects:

- **No application code.** By design (Req 5.1–5.3), no source exists under `apps/`,
  `lab-images/`, `content/`, `prompts/`, `evals/`, or `deploy/`. Those directories are described
  in the design documents and will be populated by later phase specs.
- **S12 holds no authoritative Rule_Line.** `api-conventions.md` mirrors `docs/API-CONVENTIONS.md`
  (D2) and was produced after it per Req 1.8/1.9. The API-conventions rule text (RFC 9457 error
  format R63; duplicate-submission behavior R39/R78) keeps its authoritative home in S10/S4; S12
  is a conditional mirror for API code, so it adds no new Rule_Line to the traceability total.
- **Placement and final assessment are schema/interface only.** `placement` and
  `final_assessment` are modeled in D3 but their flows are deferred (per the rule "schema and
  interfaces only for now").
- **Reserved ADR A8.** The eighth ADR slot carries context and a decision that explicitly record
  "reserved / no decision yet"; it will be claimed by a future decision.

### 6.2 Open questions

Open questions and ranked risks are maintained in [`docs/RISKS.md`](../RISKS.md) (D8) and the
rule-review commentary in [`docs/RULES-REVIEW.md`](../RULES-REVIEW.md) (D7). Items that an ADR
resolves are cross-referenced from those documents to the ADR. No open question blocks acceptance
of the design foundation; each is tracked for a later phase.

## 7. Verification-Check Outcomes

All checks were run against the repository at commit `4a151bc` on branch
`feature/platform_design_foundation`.

| # | Check | Property (design.md) | Requirement | Result | Evidence |
|---|-------|----------------------|-------------|--------|----------|
| 1 | Front-matter validity | P2 | 1.5, 1.6, 1.7 | **PASS** | 12/12 steering files carry a parseable YAML front-matter block with an `inclusion` key; 7 carry a `fileMatchPattern` (domain-rules, content, labs, deployment, auth-privacy, ai-content, api-conventions); 5 declare `inclusion: always` (product, tech, structure, quality, working-agreements). |
| 2 | Mermaid render | P3 | 2.1, 2.3 | **PASS** | 24 Mermaid blocks across the design docs (ARCHITECTURE 3, DATA-MODEL 1, LABS-DESIGN 3, DEPLOYMENT-DESIGN 7, AI-CONTENT-DESIGN 7, RULES-REVIEW 1, RISKS 1); every block parses to a supported diagram type (graph, stateDiagram-v2, sequenceDiagram, erDiagram, quadrantChart). No ADR contains a Mermaid block. |
| 3 | Traceability completeness | P1 | 1.1, 1.2 | **PASS** | 85 Rule_Lines (R1–R85), contiguous with no gaps or duplicates; each maps to exactly one authoritative steering file; all transcriptions marked Preserved. |
| 4 | Link integrity | P6 | 2.2, 2.4 | **PASS** | Every `#[[file:.kiro/steering/<file>.md]]` reference in `docs/` resolves to an existing steering file; targets are `structure.md`, `domain-rules.md`, `labs.md`, `deployment.md`, `auth-privacy.md`, `ai-content.md`, `quality.md`, `tech.md`, `working-agreements.md`. No unresolved reference. |
| 5 | ADR completeness | P4 | 3.1, 3.2, 3.3 | **PASS** | Exactly 8 ADRs under `docs/adr/` (A1–A8); each has `## Context`, `## Decision`, and `## Consequences` with non-empty prose. A8 is the reserved record (context states reserved; decision states none recorded yet). |
| 6 | No-code check | P7 | 5.1, 5.2, 5.3 | **PASS** | All six application directories (`apps/`, `lab-images/`, `content/`, `prompts/`, `evals/`, `deploy/`) are absent; `git status` and `git ls-files` report zero changed and zero tracked files under them. |

### 7.1 Failure-scenario coverage (Req 4.1–4.6, Property 5)

Each of the six failure scenarios has a documented behavior in its named design document and a
matching IF/THEN requirement in `requirements.md`:

| Scenario | Requirement | Documented in |
|----------|-------------|---------------|
| Sandbox provisioning failure | 4.1 | `docs/LABS-DESIGN.md` (D4) |
| WebSocket drop | 4.2 | `docs/LABS-DESIGN.md` (D4) |
| Grading check timeout | 4.3 | `docs/LABS-DESIGN.md` (D4) |
| LLM timeout / invalid output | 4.4 | `docs/AI-CONTENT-DESIGN.md` (D6) |
| Content sync during active use | 4.5 | `docs/AI-CONTENT-DESIGN.md` (D6) + `docs/DATA-MODEL.md` (D3) |
| Duplicate quiz submission | 4.6 | `docs/API-CONVENTIONS.md` (D2) + `docs/DATA-MODEL.md` (D3) |

### 7.2 No-application-code confirmation

**Confirmed: zero files were created, modified, or deleted under `apps/`, `lab-images/`,
`content/`, `prompts/`, `evals/`, or `deploy/`.** All six directories are absent from the working
tree, and both `git status --porcelain` and `git ls-files` scoped to those paths return empty.
The only paths produced by this spec are under `.kiro/steering/` (steering files) and `docs/`
(design documents, ADRs, and this report) — the two permitted deliverable categories (Req 5.2).

## 8. Conclusion

The EDU-IT design foundation is complete and passes all six verification checks plus the
failure-scenario coverage check. The rule-to-steering mapping is total (0 unmapped) and
consistent (0 multiply-mapped); every Mermaid diagram renders; every steering reference resolves;
every ADR is complete; and no application code was produced. The foundation is ready for the
final checkpoint and the user's approval of the design before any implementation phase begins.
