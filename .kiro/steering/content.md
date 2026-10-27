---
inclusion: fileMatch
fileMatchPattern: "content/**|**/*.mdx|**/content/**|**/messages/**|**/locales/**|**/i18n/**|**/*.{fr,en}.json|**/translations/**|**/*.{fr,en}.mdx"
---

# Content Pipeline and Localization

This steering file is the single source of truth for the EDU-IT content-pipeline and
localization rules. It is included conditionally (fileMatch inclusion) when authored content and
translation files are in context. The content-pipeline and localization rules below are
transcribed verbatim from the project rules; all numbers and normative wording are preserved
unchanged. Where a rule is not self-explanatory, a one-line reason is added and marked `Reason:`.

## Localization

- The platform is bilingual: French and English (FR/EN).
  Reason: all learner-facing content and the UI are offered in both languages.
- All learner-facing content and the UI are offered in both languages.
- `apps/web` uses the Next.js App Router UI with `/fr` and `/en` routes via next-intl.
- `apps/web` renders MDX with an allowlist of components.
- `apps/api` serves MDX source per locale.
- Learner-facing errors are translated and actionable; details stay in logs.
- `Mailer`: console dev, SMTP prod; FR/EN templates.

## Content Authoring and Storage

- `content/` holds authored learning content (MDX per locale).
- Catalog content entities: `track`, `module`, `lesson`, `lab`, `lab_task`, `lab_check`,
  `project`, `rubric`, `rubric_criterion`, `quiz`, `question`, `question_option`, `concept`,
  `prerequisite`.

## Content Pipeline (Generation and Review)

- Content is drafted by AI and approved by experts.
  Reason: AI generation is ahead-of-time; an expert reviewer approves before content is served.
- The LLM provider is used for ahead-of-time content generation only, never on a learner's
  request path.
  Reason: keeps the learner experience independent of LLM latency and availability.
- A variant is served only after it is Verified (schema + independent answer + sandbox checks)
  and Approved by a reviewer; only approved variants are served.
- A variant is Withdrawn on abnormal correct-rate/time or when reported.

## Provenance and Versioning

- Provenance is recorded on every content item: `origin`, `model`, `prompt_id`,
  `prompt_version`, `generation_date`, `reviewed_by`, `reviewed_at`; plus a `content_version`
  hash and a `stable_id`.

## Content Sync

- Content sync upsert is idempotent, keyed by stable ID; edits never reset progress; existing
  completions are kept; new attempts use the new version; removed items are archived rather than
  deleted.
