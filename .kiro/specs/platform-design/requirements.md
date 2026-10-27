# Requirements Document

## Introduction

This is a **design-foundation (meta) spec** for the EDU-IT platform. Its deliverables are
steering files and design documents only: no application source code is produced here. The
requirements below govern the **production and verification** of those deliverables, not the
runtime behavior of the EDU-IT application itself.

The deliverables are: the steering files (S1–S12), the `docs/` design documents (D1–D8), the
architecture decision records (A1–A8), and the phase-0 report (R1). The requirements cover five
concerns: faithful transcription of project rules into traceable steering files, correct and
resolvable diagrams and cross-references, complete ADRs, documented behavior for every failure
scenario captured as IF/THEN requirements, and the absence of any application code.

These requirements do not restate the normative rule text; the steering files are the single
source of that text. The requirements instead define what must be true of the produced artifacts
so that the Correctness Properties stated in `design.md` hold. Requirement numbering is kept
consistent with the requirement IDs those properties forward-reference, preserving end-to-end
traceability.

## Glossary

- **EDU-IT_Spec**: The design-foundation (meta) spec whose production this document governs. It
  is the actor that produces and verifies the deliverables.
- **Steering_File**: A Markdown file under `.kiro/steering/` carrying front matter and project
  rules; the single source of truth for a rule domain (S1–S12).
- **Front_Matter**: The structured header block at the top of a steering file (parsed as YAML)
  that declares inclusion metadata.
- **fileMatch_Pattern**: A front-matter field declaring the file-path glob that activates a
  steering file for conditional (fileMatch) inclusion.
- **Design_Document**: A Markdown document under `docs/` capturing part of the technical design
  (D1–D8).
- **ADR**: An Architecture Decision Record under `docs/adr/`, each with context, decision and
  consequences sections (A1–A8).
- **Phase_0_Report**: The verification report at `docs/reports/phase-0.md` (R1) that records the
  traceability table and the outcome of every verification check.
- **Steering_Reference**: A `#[[file:.kiro/steering/<file>.md]]` include used by design documents
  to link steering instead of copying rule text.
- **Mermaid_Diagram**: A fenced Mermaid code block in any deliverable.
- **Traceability_Table**: The table in the Phase_0_Report mapping every rule line in the spec
  request to the steering file that holds it.
- **Rule_Line**: A single normative rule drawn from the project rules captured in the spec
  request.
- **Normative_Wording**: The imperative clauses and all literal values (numbers, identifiers,
  enumerated options, thresholds, and names) of a Rule_Line, excluding the optional one-line
  reason a steering file may add where a rule is not self-explanatory.
- **Failure_Scenario**: One of the six documented failure conditions (sandbox provisioning
  failure, WebSocket drop, grading check timeout, LLM timeout or invalid output, content sync
  during active use, duplicate quiz submission).
- **Application_Code**: Any file under `apps/`, `lab-images/`, `content/`, `prompts/`, `evals/`,
  or `deploy/`.

## Requirements

### Requirement 1: Faithful, Traceable Steering Files

**User Story:** As a platform maintainer, I want the project rules transcribed into traceable
steering files with valid front matter, so that every downstream spec inherits a single,
faithful source of truth.

#### Acceptance Criteria

1. WHEN the EDU-IT_Spec generates the Traceability_Table in `docs/reports/phase-0.md`, THE EDU-IT_Spec SHALL map each Rule_Line in the spec request to exactly one target Steering_File.
2. WHEN the EDU-IT_Spec transcribes a Rule_Line into a Steering_File, THE EDU-IT_Spec SHALL preserve every number and all Normative_Wording from that Rule_Line unchanged, adding only an optional one-line reason where the rule is not self-explanatory.
3. IF any Rule_Line is unmapped or orphaned after the Traceability_Table is generated, THEN THE EDU-IT_Spec SHALL reject the design foundation as incomplete and indicate which Rule_Line lacks a Steering_File mapping.
4. IF any Rule_Line maps to more than one Steering_File, THEN THE EDU-IT_Spec SHALL reject the design foundation as inconsistent and indicate which Rule_Line is mapped multiple times.
5. WHEN a Steering_File (S1–S12) is produced, THE EDU-IT_Spec SHALL include valid Front_Matter, defined as a parseable YAML block containing the required inclusion keys.
6. IF a Steering_File's Front_Matter is not parseable YAML or is missing a required inclusion key, THEN THE EDU-IT_Spec SHALL reject that Steering_File and indicate the parse failure or the missing inclusion key.
7. WHERE a Steering_File requires conditional inclusion, THE EDU-IT_Spec SHALL include a fileMatch_Pattern in that Steering_File's Front_Matter.
8. WHILE the design document is incomplete, THE EDU-IT_Spec SHALL NOT produce S12 (`api-conventions.md`).
9. WHEN the design document is complete, THE EDU-IT_Spec SHALL produce S12 (`api-conventions.md`) with valid Front_Matter and a fileMatch_Pattern.

### Requirement 2: Rendering Diagrams and Resolvable References

**User Story:** As a reviewer of the design, I want every diagram to render and every steering
reference to resolve, so that the design documents are readable and their links are not broken.

#### Acceptance Criteria

1. WHEN a deliverable containing one or more Mermaid_Diagram blocks is validated, THE EDU-IT_Spec SHALL confirm that each Mermaid_Diagram parses and renders to a visual output with zero rendering errors reported by the Mermaid renderer.
2. WHEN a deliverable containing one or more Steering_Reference includes is validated, THE EDU-IT_Spec SHALL confirm that each Steering_Reference of the form `#[[file:.kiro/steering/<file>.md]]` resolves to a Steering_File that exists under `.kiro/steering/`.
3. IF a Mermaid_Diagram fails to parse or render, THEN THE EDU-IT_Spec SHALL report a validation failure identifying the deliverable and the position of the failing Mermaid_Diagram, and SHALL mark the affected deliverable as not passing validation without modifying the deliverable.
4. IF a Steering_Reference points to a path that does not resolve to an existing Steering_File under `.kiro/steering/`, THEN THE EDU-IT_Spec SHALL report a validation failure identifying the deliverable and the unresolved reference path, and SHALL mark the affected deliverable as not passing validation without modifying the deliverable.

### Requirement 3: Complete Architecture Decision Records

**User Story:** As an architect, I want every architecture decision recorded with full
reasoning, so that future contributors understand the context and trade-offs behind each choice.

#### Acceptance Criteria

1. THE EDU-IT_Spec SHALL produce each ADR A1 through A8 with a context section, a decision section and a consequences section, where each of the three sections contains at least one non-empty sentence of prose and no section consists solely of a heading or placeholder text.
2. THE EDU-IT_Spec SHALL store each ADR A1 through A8 as a separate document under `docs/adr/`, resulting in exactly eight ADR documents each addressing exactly one of the eight decision topics (versions, translation storage, task queue, session storage, MDX rendering, decay computation, LLM provider and data residency, and the one reserved topic).
3. WHERE ADR A8 is the reserved topic, THE EDU-IT_Spec SHALL produce A8 with the same three sections present as all other ADRs, with its context section stating that the topic is reserved for future use and its decision section stating that no decision is recorded yet.

### Requirement 4: Documented Failure Scenarios

**User Story:** As a platform operator, I want the behavior of every failure scenario documented
and required, so that the system's response to each failure is specified before implementation.

#### Acceptance Criteria

1. IF sandbox provisioning fails, THEN THE EDU-IT_Spec SHALL document in `docs/LABS-DESIGN.md` that the system surfaces a translated error, leaves no partial session running, lets the reaper clean orphaned resources, and applies provisioning rate-limits and retries bounded by the maximum values stated in that same document.
2. IF a terminal WebSocket connection drops, THEN THE EDU-IT_Spec SHALL document in `docs/LABS-DESIGN.md` that the system re-verifies session ownership on reconnect, presents a reconnect indication, drains active sockets on graceful shutdown, and persists no terminal I/O.
3. IF a grading check does not return within the 15 s target, THEN THE EDU-IT_Spec SHALL document in `docs/LABS-DESIGN.md` that grading is bounded (results within a 15 s target, executed at most once per 10 s per session), the timeout yields a translated "could not complete" feedback instead of raw output, and the run is recorded with a failed status.
4. IF an LLM request times out or returns invalid output, THEN THE EDU-IT_Spec SHALL document in `docs/AI-CONTENT-DESIGN.md` that retries are bounded by the maximum stated in that document, invalid output is never auto-corrected, the item is flagged for review, the path advisor falls back to rule-based ordering, and job cost and tokens are still recorded.
5. IF content is synced while an item is in active use, THEN THE EDU-IT_Spec SHALL document in `docs/AI-CONTENT-DESIGN.md` and `docs/DATA-MODEL.md` that the upsert is idempotent keyed by stable ID, edits never reset progress, existing completions are kept, new attempts use the new version, and removed items are archived rather than deleted.
6. IF a duplicate quiz submission is received, THEN THE EDU-IT_Spec SHALL document in `docs/API-CONVENTIONS.md` and `docs/DATA-MODEL.md` that an idempotency key dedupes the submission, re-answering the same question within 24 hours of the first answer yields no additional mastery gain, and the best attempt still counts for completion.

### Requirement 5: No Application Code Produced

**User Story:** As a maintainer, I want this spec to produce no application code, so that the
design foundation stays a documentation-only phase and implementation is deferred to later specs.

#### Acceptance Criteria

1. THE EDU-IT_Spec SHALL create, modify, or delete zero files whose path is located under any of the directories `apps/`, `lab-images/`, `content/`, `prompts/`, `evals/`, or `deploy/` (collectively, Application_Code).
2. THE EDU-IT_Spec SHALL produce as deliverables only steering files and design documents, and SHALL produce zero files outside those two deliverable categories.
3. IF a task or action within the EDU-IT_Spec would create, modify, or delete any Application_Code file, THEN THE EDU-IT_Spec SHALL reject that action, leave all Application_Code directories unchanged, and record an indication identifying the rejected path.
