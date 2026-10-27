# ADR 0002 (A2): Translation storage — how FR/EN learner-facing fields and UI keys are stored

## Status

Accepted.

## Context

EDU-IT is a bilingual (French/English) re-learning platform, and `apps/web` serves `/fr` and
`/en` routes via next-intl while `apps/api` serves MDX source per locale. Two distinct kinds of
text must be localized: static UI strings (labels, buttons, messages rendered by the web app)
and learner-facing content fields that live in the data model (for example catalog and item
text drawn from the entity groups in `docs/DATA-MODEL.md` (D3), such as `track`, `module`,
`lesson`, `quiz`, and `question` text). These two kinds of text have different lifecycles: UI
keys are owned by the application and shipped with the frontend, while learner-facing content
fields are authored through the content pipeline, carry provenance, and are versioned by
`content_version` and `stable_id`. Without an explicit decision, FR and EN text could be stored
inconsistently — mixing UI concerns with content concerns — which complicates localization,
export, and the content-sync behavior that keys idempotent upserts by stable ID.

This decision is grounded in the bilingual platform and localization rules described in
`#[[file:.kiro/steering/tech.md]]` and `#[[file:.kiro/steering/content.md]]`, and in the schema
described in `docs/DATA-MODEL.md` (D3).

## Decision

Store the two kinds of translated text separately according to their owner. UI keys — the static
strings rendered by `apps/web` — are stored as next-intl message catalogs keyed per locale
(`/fr` and `/en`), owned by the web application and shipped with the frontend. Learner-facing
content fields are stored in the data model alongside their content item, carrying both the FR
and EN values for each localizable field so that a single content record keyed by its
`stable_id` holds both locales and is versioned together by `content_version`. `apps/api` serves
the MDX source for the requested locale from these content records. This keeps UI translation and
content translation in distinct stores while ensuring that every learner-facing field exists in
both French and English and that both locales move together through the content pipeline,
provenance, export, and the idempotent content-sync upsert keyed by stable ID.

## Consequences

Separating UI keys from content fields matches each kind of text to its owner and lifecycle: UI
strings ship with the frontend and are versioned with it, while content fields are versioned by
`content_version` and `stable_id` and flow through the content pipeline with provenance intact.
Because both FR and EN values live on the same content record, content sync remains a single
idempotent upsert per item and export/deletion cover both locales together. The cost is that
authors and reviewers must supply both locales for every learner-facing field, and the two stores
(message catalogs and content records) must each be maintained; a missing translation surfaces as
a gap in one store rather than silently falling back. This arrangement keeps localization
consistent across `/fr` and `/en` and keeps content-sync behavior predictable.
