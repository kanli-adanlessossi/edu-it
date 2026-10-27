# ADR 0005: MDX Rendering — Allowlisted Component Rendering

- **ID:** A5
- **Status:** Accepted
- **Date:** 2025-01-01
- **Decision topic:** MDX rendering (allowlisted component rendering approach)

## Context

Learner-facing learning content is authored as MDX per locale and lives under `content/`
(MDX per locale). `apps/web` renders MDX with an allowlist of components, and `apps/api`
serves MDX source per locale. Because MDX can embed arbitrary components and expressions,
rendering untrusted or author-supplied MDX without constraint would widen the attack surface
(script injection, unvetted interactive widgets) and make the rendered output unpredictable
across the `/fr` and `/en` routes. The platform needs a single, predictable rendering contract
so that every lesson renders the same way regardless of locale, and so that only vetted,
accessible components can appear in content.

This decision is grounded in the repository-structure and technology rules captured in steering
— see #[[file:.kiro/steering/structure.md]], #[[file:.kiro/steering/content.md]], and
#[[file:.kiro/steering/tech.md]] — and is described at the architecture level in
`docs/ARCHITECTURE.md` (D1). The AI-content and content pipeline (D5/D6 and
`docs/DATA-MODEL.md` D3) treat MDX as the stored content payload keyed by `stable_id`.

## Decision

`apps/web` renders MDX through an explicit component allowlist: only components on the allowlist
are made available to MDX during rendering, and any component reference outside the allowlist is
not rendered as an executable component. MDX is authored per locale and served as source per
locale by `apps/api`, while `apps/web` owns the allowlist and performs the render. The allowlist
is the single rendering contract shared by the `/fr` and `/en` routes, so the same content
renders identically in both locales. No MDX-embedded arbitrary script or unlisted component is
executed.

## Consequences

Content authors are limited to the vetted component set, which keeps rendered content
predictable, accessible, and safe from injection, at the cost of requiring an allowlist update
(a reviewed `apps/web` change) whenever a genuinely new content component is needed. Because the
allowlist is defined in `apps/web` rather than in individual content files, the security and
accessibility posture is enforced centrally and uniformly across both locales. Content stored in
`content/` remains plain MDX keyed by `stable_id`, so the content-sync behavior (idempotent
upsert, existing completions kept) in `docs/DATA-MODEL.md` (D3) and `docs/AI-CONTENT-DESIGN.md`
(D6) is unaffected by the rendering choice. This decision flows back into the relevant steering
files per #[[file:.kiro/steering/working-agreements.md]].
