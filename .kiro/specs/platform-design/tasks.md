# Implementation Plan: EDU-IT Platform Design Foundation

## Overview

This is a **design-foundation (meta) spec**. Every task below produces documentation artifacts
only — steering files, design documents, ADRs, and a phase-0 report. No task writes, modifies, or
tests application source code, and no task creates files under `apps/`, `lab-images/`,
`content/`, `prompts/`, `evals/`, or `deploy/`.

The plan is grouped into four milestones in the user-requested order: (1) steering files,
(2) traceability check, (3) design documents and ADRs, (4) phase-0 report. Each milestone ends
with a checkpoint. Because the design document contains no "Correctness Properties" implemented
in code (its properties are verification checks over documents), there are no property-based
test sub-tasks; verification is performed through the documented checks in the phase-0 report.

---

## Tasks

### Milestone 1 — Steering files

- [x] 1. Author the always-included steering files with valid front matter, transcribing project rules verbatim
  - [x] 1.1 Author `.kiro/steering/product.md` (always included)
    - Add parseable YAML front matter with the required inclusion keys (always-on inclusion).
    - Transcribe the product scope and principles rules verbatim: change no numbers and no normative wording; add a one-line reason only where a rule is not self-explanatory.
    - _Requirements: 1.1, 1.2, 1.5, 1.6_

  - [x] 1.2 Author `.kiro/steering/tech.md` (always included)
    - Add parseable YAML front matter with the required inclusion keys (always-on inclusion).
    - Transcribe the technology-choice rules verbatim; preserve all numbers and normative wording.
    - _Requirements: 1.1, 1.2, 1.5, 1.6_

  - [x] 1.3 Author `.kiro/steering/structure.md` (always included)
    - Add parseable YAML front matter with the required inclusion keys (always-on inclusion).
    - Transcribe the repository-structure and layering rules verbatim; preserve all numbers and normative wording.
    - _Requirements: 1.1, 1.2, 1.5, 1.6_

  - [x] 1.4 Author `.kiro/steering/quality.md` (always included)
    - Add parseable YAML front matter with the required inclusion keys (always-on inclusion).
    - Transcribe the quality-bar and testing rules verbatim; preserve all numbers and normative wording.
    - _Requirements: 1.1, 1.2, 1.5, 1.6_

  - [x] 1.5 Author `.kiro/steering/working-agreements.md` (always included)
    - Add parseable YAML front matter with the required inclusion keys (always-on inclusion).
    - Transcribe the working-agreements and process rules verbatim; preserve all numbers and normative wording.
    - _Requirements: 1.1, 1.2, 1.5, 1.6_

- [x] 2. Author the conditional (fileMatch) steering files with valid front matter and fileMatch patterns, transcribing project rules verbatim
  - [x] 2.1 Author `.kiro/steering/domain-rules.md` (fileMatch: domain and application code, content)
    - Add parseable YAML front matter including a `fileMatch` pattern covering domain/application code and content.
    - Transcribe the domain rules (unlocking, quizzes, labs, projects, mastery, review, hints, placement) verbatim; preserve all numbers and normative wording.
    - _Requirements: 1.1, 1.2, 1.5, 1.6, 1.7_

  - [x] 2.2 Author `.kiro/steering/content.md` (fileMatch: content, translation files)
    - Add parseable YAML front matter including a `fileMatch` pattern covering content and translation files.
    - Transcribe the content-pipeline and localization rules verbatim; preserve all numbers and normative wording.
    - _Requirements: 1.1, 1.2, 1.5, 1.6, 1.7_

  - [x] 2.3 Author `.kiro/steering/labs.md` (fileMatch: lab images, sandbox, lab and terminal code)
    - Add parseable YAML front matter including a `fileMatch` pattern covering lab images, sandbox, and lab/terminal code.
    - Transcribe the labs, sandbox, and terminal rules verbatim; preserve all numbers and normative wording.
    - _Requirements: 1.1, 1.2, 1.5, 1.6, 1.7_

  - [x] 2.4 Author `.kiro/steering/deployment.md` (fileMatch: deploy, Dockerfiles, CI)
    - Add parseable YAML front matter including a `fileMatch` pattern covering deploy, Dockerfiles, and CI.
    - Transcribe the deployment and Kubernetes-topology rules verbatim; preserve all numbers and normative wording.
    - _Requirements: 1.1, 1.2, 1.5, 1.6, 1.7_

  - [x] 2.5 Author `.kiro/steering/auth-privacy.md` (fileMatch: auth, accounts, privacy, users)
    - Add parseable YAML front matter including a `fileMatch` pattern covering auth, accounts, privacy, and users.
    - Transcribe the authentication and privacy rules verbatim; preserve all numbers and normative wording.
    - _Requirements: 1.1, 1.2, 1.5, 1.6, 1.7_

  - [x] 2.6 Author `.kiro/steering/ai-content.md` (fileMatch: prompts, evals, generation, LLM, review and advisor code)
    - Add parseable YAML front matter including a `fileMatch` pattern covering prompts, evals, generation, LLM, and review/advisor code.
    - Transcribe the AI content generation and review rules verbatim; preserve all numbers and normative wording.
    - _Requirements: 1.1, 1.2, 1.5, 1.6, 1.7_

- [x] 3. Checkpoint — steering files parse and front matter is valid
  - Confirm every steering file (S1–S11) parses, its front matter is valid parseable YAML with the required inclusion keys, and a `fileMatch` pattern is present on every conditional file (domain-rules, content, labs, deployment, auth-privacy, ai-content).
  - Confirm S12 (`api-conventions.md`) has NOT yet been produced (it is deferred to Milestone 3, after the design docs).
  - Note: faithful transcription (Req 1.2 — verbatim preservation of all numbers and normative wording) is NOT fully verified at this checkpoint. It is asserted during authoring in Milestone 1 but is formally verified in Milestone 2 (task 4.1). This checkpoint confirms only parse validity, front-matter validity, and `fileMatch` presence; it does not assume faithful transcription has been verified.
  - Ensure all checks pass, ask the user if questions arise.
  - _Requirements: 1.3, 1.4, 1.5, 1.6, 1.7, 1.8_

### Milestone 2 — Traceability check

- [x] 4. Build and validate the rule-to-steering traceability mapping
  - [x] 4.1 Build the traceability table mapping every Rule_Line to exactly one steering file
    - Enumerate every rule line in the spec request and map each to exactly one target steering file.
    - For each mapping, confirm the transcription in Milestone 1 changed no numbers and no normative wording.
    - Record the draft table for inclusion in the phase-0 report (final location is `docs/reports/phase-0.md`, written in Milestone 4).
    - _Requirements: 1.1, 1.2_

  - [x] 4.2 Flag unmapped and multiply-mapped rule lines
    - Identify and list any Rule_Line with no steering-file mapping; mark the foundation incomplete and indicate the unmapped line.
    - Identify and list any Rule_Line mapped to more than one steering file; mark the foundation inconsistent and indicate the multiply-mapped line.
    - _Requirements: 1.3, 1.4_

- [x] 5. Checkpoint — traceability is total and faithful
  - Confirm every Rule_Line maps to exactly one steering file with no unmapped or multiply-mapped lines, and that transcription preserved all numbers and normative wording.
  - Ensure all checks pass, ask the user if questions arise.
  - _Requirements: 1.1, 1.2, 1.3, 1.4_

### Milestone 3 — Design documents and ADRs

- [x] 6. Author the architecture and conventions design documents
  - [x] 6.1 Author `docs/ARCHITECTURE.md` (D1)
    - Document repository structure, component responsibilities, and layering with a Mermaid diagram; link steering via `#[[file:.kiro/steering/structure.md]]`.
    - _Requirements: 2.1, 2.2_

  - [x] 6.2 Author `docs/API-CONVENTIONS.md` (D2)
    - Document RFC 9457 problem details (`type`, `title`, `status`, `detail`, `instance`), pagination strategy, API versioning scheme, stable ID format, and idempotency keys for mutations.
    - Document failure scenario 4.6 (duplicate quiz submission): idempotency key dedupes, re-answering the same question within 24 hours yields no additional mastery gain, best attempt still counts for completion.
    - _Requirements: 2.1, 2.2, 4.6_

  - [x] 6.3 Produce `.kiro/steering/api-conventions.md` (S12) after the design doc
    - Add parseable YAML front matter including a `fileMatch` pattern on API code.
    - Mirror the API conventions from `docs/API-CONVENTIONS.md`; produce only after D2 is complete per Req 1.8/1.9.
    - _Requirements: 1.8, 1.9_

- [x] 7. Author the data-model design document
  - [x] 7.1 Author `docs/DATA-MODEL.md` (D3)
    - Produce the full schema as a Mermaid ER diagram including identity, catalog, provenance, progress, mastery and review, AI/review ops (reviews, variants, item statistics, path recommendations), and assessments; ensure every personal-data table is covered by export, deletion, and retention.
    - Document failure scenario 4.5 (content sync during active use): idempotent upsert keyed by stable ID, edits never reset progress, existing completions kept, new attempts use the new version, removed items archived not deleted.
    - Document failure scenario 4.6 (duplicate quiz submission) at the data level: idempotency dedupe, no additional mastery within 24 hours, best attempt counts for completion.
    - Link steering via `#[[file:.kiro/steering/auth-privacy.md]]`.
    - _Requirements: 2.1, 2.2, 4.5, 4.6_

- [x] 8. Author the labs design document with failure scenarios
  - [x] 8.1 Author `docs/LABS-DESIGN.md` (D4)
    - Document the `SandboxProvider` interface signatures; add sequence diagrams for start lab → terminal → grade → result, including timeout/TTL and grading isolation.
    - Document the security model per provider (Docker dev-only, Kubernetes prod, Fake tests).
    - Document failure scenario 4.1 (sandbox provisioning failure): translated error, no partial session left running, reaper cleans orphans, provisioning rate-limited and retried bounded by the maximum values stated in the document.
    - Document failure scenario 4.2 (WebSocket drop): ownership re-checked on reconnect, reconnect indication shown, graceful shutdown drains sockets, no terminal I/O persisted.
    - Document failure scenario 4.3 (grading check timeout): grading bounded (15 s target, at most once per 10 s per session), timeout yields a translated "could not complete" feedback not raw output, run recorded as failed.
    - Link steering via `#[[file:.kiro/steering/labs.md]]`.
    - _Requirements: 2.1, 2.2, 4.1, 4.2, 4.3_

- [x] 9. Author the deployment design document
  - [x] 9.1 Author `docs/DEPLOYMENT-DESIGN.md` (D5)
    - Document the Kubernetes topology per target (EKS, AKS, on-premise): namespaces, node pools, network policies for labs and platform, RBAC, and the ingress and WebSocket path; include topology Mermaid diagrams.
    - Link steering via `#[[file:.kiro/steering/deployment.md]]`.
    - _Requirements: 2.1, 2.2_

- [x] 10. Author the AI-content design document with failure scenarios
  - [x] 10.1 Author `docs/AI-CONTENT-DESIGN.md` (D6)
    - Document the `LLMProvider` interface signatures; document generation stages and review gates; add a variant-lifecycle state diagram; document path-advisor I/O and its fallback; specify what data is allowed in LLM requests.
    - Document failure scenario 4.4 (LLM timeout/invalid output): bounded retries by the maximum stated in the document, invalid output never auto-corrected, item flagged for review, path advisor falls back to rule-based ordering, job cost and tokens still recorded.
    - Document failure scenario 4.5 (content sync during active use) in coordination with D3: idempotent upsert keyed by stable ID, edits never reset progress, existing completions kept, new attempts use new version, removed items archived not deleted.
    - Link steering via `#[[file:.kiro/steering/ai-content.md]]`.
    - _Requirements: 2.1, 2.2, 4.4, 4.5_

- [x] 11. Author the rules-review and risks design documents
  - [x] 11.1 Author `docs/RULES-REVIEW.md` (D7)
    - Confirm or challenge each domain rule, with a worked example tracing one learner's mastery points through the seed track, including one hint reveal and one overdue review.
    - Link steering via `#[[file:.kiro/steering/domain-rules.md]]`.
    - _Requirements: 2.1, 2.2_

  - [x] 11.2 Author `docs/RISKS.md` (D8)
    - Document ranked risks and open questions with recommendations.
    - _Requirements: 2.1, 2.2_

- [x] 12. Author the architecture decision records
  - [x] 12.1 Author ADRs A1–A4 under `docs/adr/`
    - A1 versions (pin current stable versions of frameworks and test tools); A2 translation storage (FR/EN learner-facing fields and UI keys); A3 task queue (concrete choice behind `TaskQueue`); A4 session storage (server-side session backing).
    - Each ADR has a context section, a decision section, and a consequences section, each with at least one non-empty sentence of prose and no section consisting solely of a heading or placeholder.
    - _Requirements: 3.1, 3.2_

  - [x] 12.2 Author ADRs A5–A8 under `docs/adr/`
    - A5 MDX rendering (allowlisted component rendering); A6 decay computation (deterministic clock-based strategy); A7 LLM provider and data residency (provider behind `LLMProvider`, DPA, EU residency); A8 reserved topic.
    - Each of A5–A7 has context, decision, and consequences with non-empty prose. For A8, produce the same three sections: context states the topic is reserved for future use and decision states no decision is recorded yet.
    - _Requirements: 3.1, 3.2, 3.3_

- [x] 13. Validate diagrams and steering references across all design docs and ADRs
  - [x] 13.1 Verify every Mermaid diagram renders and every steering reference resolves
    - Validate that each Mermaid diagram in every deliverable parses and renders with zero errors; on failure, identify the deliverable and the position of the failing diagram without modifying it.
    - Validate that each `#[[file:.kiro/steering/<file>.md]]` reference resolves to an existing steering file; on failure, identify the deliverable and the unresolved reference path without modifying it.
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

- [x] 14. Checkpoint — design docs and ADRs complete
  - Confirm all design documents (D1–D8) and ADRs (A1–A8) are complete, every Mermaid diagram renders, every steering reference resolves, every ADR has context/decision/consequences, failure scenarios 4.1–4.6 are documented in their named documents, and S12 was produced after the design docs.
  - Confirm ADR cardinality per Req 3.2: exactly eight ADR documents (A1–A8) exist under `docs/adr/`, each addressing exactly one of the eight decision topics.
  - Ensure all checks pass, ask the user if questions arise.
  - _Requirements: 1.8, 1.9, 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 3.3, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_

### Milestone 4 — Phase report (final)

- [x] 15. Write the phase-0 report
  - [x] 15.1 Author `docs/reports/phase-0.md` (R1)
    - Include the traceability table (from Milestone 2), a description of what was built, how to verify with exact commands, deviations with ADR links, known gaps, and open questions.
    - Record the outcome of each verification check: front-matter validity, Mermaid render, traceability completeness, link integrity, ADR completeness, and the no-code check.
    - Confirm no application code was produced: zero files created, modified, or deleted under `apps/`, `lab-images/`, `content/`, `prompts/`, `evals/`, or `deploy/`.
    - _Requirements: 5.1, 5.2, 5.3_

- [x] 16. Final checkpoint — await user approval of the design
  - Confirm the phase-0 report is complete and the no-application-code check passed (Req 5.1–5.3).
  - STOP and WAIT for the user's explicit approval of the design foundation. Do NOT start Phase 1; this workflow produces design and planning artifacts only.
  - Ensure all checks pass, ask the user if questions arise.
  - _Requirements: 5.1, 5.2, 5.3_

## Notes

- This spec is documentation-only: every task produces steering files, design documents, ADRs,
  or the phase-0 report. No task writes, modifies, or tests application code, and no task creates
  files under `apps/`, `lab-images/`, `content/`, `prompts/`, `evals/`, or `deploy/`.
- There are no property-based or unit test sub-tasks because no code is produced; the design's
  Correctness Properties are verified through the documented checks recorded in the phase-0 report.
- S12 (`api-conventions.md`) is intentionally produced in Milestone 3 (task 6.3), after
  `docs/API-CONVENTIONS.md`, to satisfy Req 1.8/1.9.
- Each task references the requirement IDs it satisfies for end-to-end traceability.
- Checkpoints close each milestone; the final checkpoint stops for user approval and must not
  begin implementation.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.3", "1.4", "1.5", "2.1", "2.2", "2.3", "2.4", "2.5", "2.6"] },
    { "id": 1, "tasks": ["4.1"] },
    { "id": 2, "tasks": ["4.2"] },
    { "id": 3, "tasks": ["6.1", "6.2", "7.1", "8.1", "9.1", "10.1", "11.1", "11.2", "12.1", "12.2"] },
    { "id": 4, "tasks": ["6.3"] },
    { "id": 5, "tasks": ["13.1"] },
    { "id": 6, "tasks": ["15.1"] }
  ]
}
```
