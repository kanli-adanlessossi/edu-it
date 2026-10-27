---
inclusion: always
---

# Product Scope and Principles

This steering file is the single source of truth for EDU-IT's product scope and
guiding principles. It is included in every interaction (always-on inclusion). Rule
text below is transcribed verbatim from the project rules; numbers and normative
wording are preserved unchanged. Where a rule is not self-explanatory, a one-line
reason is added and marked `Reason:`.

## Product Scope

EDU-IT is a bilingual (French/English) re-learning platform for IT professionals who
are losing hands-on skills as AI agents take over technical work. Learners rebuild
skills step by step and advance only by proving mastery through quizzes, hands-on labs
(real terminals, graded code) and projects. Content is drafted by AI and approved by
experts. The platform runs on Kubernetes (EKS, AKS or on-premise).

## Principles

- Learners rebuild skills step by step and advance only by proving mastery.
  Reason: progression is gated on demonstrated mastery, not on time spent or content viewed.

- Mastery is proven through three mechanisms: quizzes, hands-on labs (real terminals,
  graded code) and projects.

- The platform is bilingual: French and English (FR/EN).
  Reason: all learner-facing content and the UI are offered in both languages.

- Content is drafted by AI and approved by experts.
  Reason: AI generation is ahead-of-time; an expert reviewer approves before content is served.

- The LLM provider is used for ahead-of-time content generation only, never on a
  learner's request path.
  Reason: keeps the learner experience independent of LLM latency and availability.

- The platform runs on Kubernetes and is portable across EKS, AKS and on-premise
  targets.
