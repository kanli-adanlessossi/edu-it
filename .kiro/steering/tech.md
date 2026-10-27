---
inclusion: always
---

# Technology Choices

The technology-choice rules below are transcribed verbatim from the EDU-IT platform design
foundation. All names, numbers, and normative wording are preserved unchanged. Where a rule is
not self-explanatory, a one-line reason is added and marked `Reason:`.

## Platform

- EDU-IT is a bilingual (French/English) re-learning platform for IT professionals.
- The platform runs on Kubernetes (EKS, AKS or on-premise).
- Portable across EKS, AKS and on-premise using standard Kubernetes APIs only; provider
  differences live in Helm values.

## Components

- `apps/web`: Next.js App Router UI, `/fr` and `/en` routes via next-intl, xterm.js terminal,
  Monaco editor, renders MDX with an allowlist of components, forms validated with Zod.
- `apps/api`: FastAPI boundary: authentication and sessions, learning endpoints, serves MDX
  source per locale, hosts the authenticated terminal WebSocket, enqueues jobs. Validates with
  Pydantic v2.
- `apps/worker`: Task-queue consumer: lab provisioning and grading orchestration, AI generation
  jobs, variant-pool refill, spaced-review and decay computation, reaper for expired sessions.
- shared Python package: Domain and application code reused by api and worker; framework-free
  domain.
- PostgreSQL: System of record for learners, content, progress, mastery, reviews, variants,
  statistics, recommendations, assessments.
- Redis: Task queue backing and ephemeral/session state (final queue choice is an ADR).
- Object storage: Behind `ObjectStorage`: lab artifacts, exports.
- LLM provider: Behind `LLMProvider`: ahead-of-time content generation only, never on a
  learner's request path.
- Sandbox: Behind `SandboxProvider`: Docker (dev only), Kubernetes (prod), fake (tests).

## Data Stores and External Systems

- PostgreSQL is the system of record.
- Redis backs the task queue and holds ephemeral/session state; the final queue choice is an ADR.
- Object storage is S3 or Azure Blob, behind the `ObjectStorage` port.
- The LLM provider sits behind the `LLMProvider` port and performs ahead-of-time content
  generation only, never on a learner's request path.
- The sandbox sits behind the `SandboxProvider` port: Docker (dev only), Kubernetes (prod),
  fake (tests).

## External-System Ports

Every external system sits behind an interface with a fake. The ports defined in the domain are:
`SandboxProvider`, `Mailer`, `ObjectStorage`, `Clock`, `TaskQueue`, `LLMProvider`, and the
`ProjectReviewer` extension point. Each has a fake.

- `Clock`: injected; domain never reads wall clock directly.
- `SandboxProvider`: implementations are Docker (dev only), Kubernetes (prod), Fake (tests);
  warm-pool extension point.
- `Mailer`: console dev, SMTP prod; FR/EN templates.
- `ObjectStorage`: lab artifacts, exports.
- `TaskQueue`: concrete backing chosen in ADR.
- `LLMProvider`: structured output validated against content schemas; bounded retries; records
  model, tokens, cost.
- `ProjectReviewer`: extension point: mentor, peer or AI-assisted review.

## Runtime and Topology

- Dedicated lab node pool with taints/tolerations; configurable RuntimeClass (gVisor or Kata;
  plain runc dev-only with a startup warning).
- Default-deny NetworkPolicy in `labs`; default-deny in `platform` so only api and worker reach
  PostgreSQL and Redis; worker egress limited to the LLM provider and the Kubernetes API.
- Graceful shutdown drains terminal WebSockets with a reconnect message.
- Object storage is S3 or Azure Blob.

## Pinned Decisions (ADR topics)

The following technology decisions are recorded as ADRs; the concrete values are pinned in those
records:

- Versions — pin current stable versions of all frameworks and test tools.
- Translation storage — how FR/EN learner-facing fields and UI keys are stored.
- Task queue — concrete choice behind `TaskQueue`.
- Session storage — server-side session backing.
- MDX rendering — allowlisted component rendering approach.
- Decay computation — deterministic clock-based decay strategy.
- LLM provider and data residency — provider behind `LLMProvider`, DPA and EU residency.
