# Split session module to keep `next/headers` out of the client bundle

The web app exported `SESSION_COOKIE`, `sessionStateFromResponse`, and `resolveSessionState` from a single `session.ts`, and `resolveSessionState` imports `next/headers`. Because the client-reachable `path-recommendation-api.ts` imported `SESSION_COOKIE` from that same module, the client component `path-recommendation-card.tsx` transitively pulled `next/headers` into the client bundle, which Next.js rejects at build time. The fix extracts the two server-agnostic exports into a new `session-shared.ts` and leaves `resolveSessionState` (with its `next/headers` import) in `session.ts`, then rewires every import site so client-reachable modules only touch `session-shared.ts`. This is a pure relocation of exports plus import rewiring with no logic change.

Watch for: nothing blocking. The relocation is verbatim (JSDoc, requirement references, and function bodies are byte-identical to the originals), every importer was updated, and the client path no longer reaches `next/headers` (confirmed by tracing the transitive imports).

**Verdict**: APPROVED

## High-level view

The split lands exactly on the server/client boundary. `session-shared.ts` holds only `SESSION_COOKIE` and `sessionStateFromResponse`, whose sole dependency is the type-only import of `SessionState` from `dashboard-access.ts`; it carries no server dependency. `session.ts` keeps `resolveSessionState` and the `next/headers` import, and now consumes the two relocated symbols from the shared module, so the server-side behavior is unchanged.

The import rewiring is complete and correctly targeted. Modules that need the cookie name (`path-recommendation-api.ts`, `learning-api.ts`, `dashboard/page.tsx`, `practice-page.ts`, `learning-server.ts`) now import `SESSION_COOKIE` from `session-shared.js`; modules that also need server-side resolution (`learning-server.ts`, `practice-page.ts`) keep importing `resolveSessionState` from `session.js`. The test `dashboard-access.test.ts` now imports `sessionStateFromResponse` from the shared module. A repo-wide search surfaced no other importer; the three e2e specs define their own local `SESSION_COOKIE` constant and never imported the module.

The key outcome holds: `path-recommendation-card.tsx -> path-recommendation-api.ts` reaches only `create-api-client.js`, `generated/api-client.js`, `i18n/routing.js`, `locale.js`, `path-recommendation.js`, and `session-shared.js` — none of which import `next/headers`. The generated client has no imports at all. (confirmed)

Scope and constraints are respected: all eight changed files live under `apps/web/src`, local imports keep explicit `.js` extensions, and no OpenAPI/generated-client, Dockerfile, or CI workflow files were touched in this commit.

<details>
<summary>Issues (0)</summary>

No actionable issues. The change is a clean relocation that achieves the stated outcome.

</details>

<details>
<summary>Details</summary>

## Relocation is verbatim

`SESSION_COOKIE` and `sessionStateFromResponse` moved into `session-shared.ts` with their JSDoc intact — the cookie-name comment, the `sessionStateFromResponse` doc mapping 2xx to `active` / everything else to `none`, and the Req 17.4 reference are unchanged. The new file's header comment adds the Req 8.5, 17.4, A4 references that previously headed `session.ts`, so the requirement trail is preserved rather than dropped. `sessionStateFromResponse` keeps the same `status >= 200 && status < 300` logic and `SessionState` return type. `resolveSessionState` in `session.ts` is untouched behaviorally: it still reads the cookie, resolves through the generated client, forwards the `SESSION_COOKIE=reference` cookie header, and fails closed to `'none'` on a missing cookie or a thrown call. Public signatures of all three symbols are preserved.

## Boundary trace

The concern this change exists to fix is the client bundle pulling `next/headers`. `session-shared.ts` imports only `type SessionState` from `dashboard-access.ts` (a type-only import), and `dashboard-access.ts` in turn imports only `locale.ts`, which has no imports. `create-api-client.ts` imports only the generated client; `path-recommendation.ts` imports `zod` and a local; the generated client imports nothing. So every module on the `path-recommendation-card.tsx -> path-recommendation-api.ts` path is free of `next/headers`. The remaining `next/headers` importers (`session.ts`, `learning-server.ts`, `dashboard/page.tsx`, `practice-page.ts`) are all server-only entry points and are not reachable from the client component, matching the intended posture.

## Import rewiring completeness

Every import site named in the task was updated, and the split-import sites (`learning-server.ts`, `practice-page.ts`) correctly draw `resolveSessionState` from `session.js` and `SESSION_COOKIE` from `session-shared.js` on separate lines. A repo-wide search for the three symbols and for any `session(-shared).js` importer returned no stragglers still pointing at `session.js` for the relocated symbols. The e2e Playwright specs (`lab-view-a11y`, `reviewer-a11y`, `learner-a11y`) each declare a private `const SESSION_COOKIE = 'edu_it_session'` and were never module importers, so leaving them untouched is correct.

## Verification (not re-run)

The commit message records the coder's results: `npm run build` (next build --webpack) compiled successfully with the `next/headers` boundary error gone, `npm run test` 369 passed including `dashboard-access.test.ts`, `npm run typecheck` (tsc --noEmit) passed, and `npm run lint` (eslint) passed. Per the task these suites were not re-run during review.

</details>

<details>
<summary>File map</summary>

- `apps/web/src/session-shared.ts` — new server-agnostic module holding `SESSION_COOKIE` and `sessionStateFromResponse` (moved verbatim with their JSDoc and the Req 8.5/17.4/A4 header).
- `apps/web/src/session.ts` — keeps `resolveSessionState` and the `next/headers` import; now imports the two relocated symbols from `session-shared.js`; added a comment explaining the server-only split.
- `apps/web/src/path-recommendation-api.ts` — `SESSION_COOKIE` now from `session-shared.js` (the client-reachable path).
- `apps/web/src/learning-api.ts` — `SESSION_COOKIE` now from `session-shared.js`.
- `apps/web/src/learning-server.ts` — `resolveSessionState` from `session.js`, `SESSION_COOKIE` from `session-shared.js`.
- `apps/web/src/app/[locale]/practice-page.ts` — `resolveSessionState` from `session.js`, `SESSION_COOKIE` from `session-shared.js`.
- `apps/web/src/app/[locale]/dashboard/page.tsx` — `SESSION_COOKIE` now from `session-shared.js`.
- `apps/web/src/dashboard-access.test.ts` — `sessionStateFromResponse` now from `session-shared.js`.

Full diff: `git show 2f23b85`.

</details>
