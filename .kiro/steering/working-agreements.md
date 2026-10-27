---
inclusion: always
---

# Working Agreements and Process

This steering file is the single source of truth for the EDU-IT working agreements and process
rules. The working-agreements and process rules below are transcribed verbatim from the project
rules; all numbers and normative wording are preserved. A one-line reason is added only where a
rule is not self-explanatory.

## Steering as the Single Source of Truth

- The authoritative rules live in steering files; design documents do not restate them, they
  link them so downstream specs and design documents inherit a single source of truth.
- Design documents reference steering through `#[[file:.kiro/steering/<file>.md]]` includes
  rather than copying rule content.
  <!-- Reason: linking instead of copying keeps one authoritative copy of each rule and prevents drift between documents. -->
- Every `#[[file:.kiro/steering/<file>.md]]` reference must resolve to a steering file that
  exists under `.kiro/steering/`.
- When transcribing a rule into a steering file, preserve every number and all normative
  wording from that rule unchanged, adding only an optional one-line reason where the rule is
  not self-explanatory.

## Steering File Front Matter

- Every steering file carries valid front matter, defined as a parseable YAML block containing
  the required inclusion keys.
- Where a steering file requires conditional inclusion, its front matter includes a `fileMatch`
  pattern.
- Always-included steering files declare always-on inclusion in their front matter.

## Architecture Decision Records

- Every architecture decision is recorded as an ADR under `docs/adr/`.
- Each ADR has a context section, a decision section, and a consequences section.
- Decisions recorded in an ADR flow back into the relevant steering file in the same change.
  <!-- Reason: keeping the ADR and the steering file in one change prevents the recorded rules from diverging from the decisions behind them. -->

## Design-Foundation (Meta) Phase Discipline

- The design-foundation (meta) phase produces steering files and design documents only; no
  application source code is produced in this phase.
- No files are created, modified, or deleted under `apps/`, `lab-images/`, `content/`,
  `prompts/`, `evals/`, or `deploy/` during this phase.
- The phase produces as deliverables only steering files and design documents, and produces zero
  files outside those two deliverable categories.
- `api-conventions.md` (S12) is produced only after the design document is complete.
  <!-- Reason: the API conventions steering mirrors the API-conventions design document, so it can only be authored once that document exists. -->

## Verification Before Acceptance

- Every rule line in the spec request is mapped in the traceability table to exactly one target
  steering file; no rule line is left unmapped or mapped to more than one steering file.
- Every Mermaid diagram in every deliverable renders without error.
- Every ADR contains context, decision, and consequences sections.
- Each failure scenario has a documented behavior in the design document named for it and a
  matching IF/THEN requirement in requirements.md.
- The no-code check confirms no files were created under `apps/`, `lab-images/`, `content/`,
  `prompts/`, `evals/`, or `deploy/`.
