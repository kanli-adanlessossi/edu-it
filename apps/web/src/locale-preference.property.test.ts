import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import {
  LocalePreferenceSaveError,
  resolveRenderedLocale,
  saveLocalePreference,
  type LocalePreferenceApi,
} from './locale-preference.js';
import { LOCALES, type Locale } from './locale.js';

// Property 22: A saved locale preference round-trips (Req 15.3, 15.4).
//
// This is the single numbered property-based test for the saved locale
// preference round-trip. It drives `saveLocalePreference` /
// `resolveRenderedLocale` with an arbitrary sequence of locale selections, each
// of which either succeeds or is forced to fail, against an in-memory
// `LocalePreferenceApi` fake. It asserts the two guarantees of the feature:
//   - Req 15.3: after a successful save, the stored preference reads back
//     exactly (via `resolveRenderedLocale`, which renders the saved value).
//   - Req 15.4: on a save failure, the previously stored preference is left
//     unchanged and a `LocalePreferenceSaveError` carrying that previous value
//     is raised.

/**
 * In-memory fake for the locale endpoint whose next save can be forced to fail.
 * Mirrors the generated API client's `saveLocale` / `readLocale` contract.
 */
class InMemoryLocaleApi implements LocalePreferenceApi {
  private stored: Locale | null;
  private failNext = false;

  constructor(initial: Locale | null = null) {
    this.stored = initial;
  }

  failNextSave(): void {
    this.failNext = true;
  }

  async readLocale(): Promise<Locale | null> {
    return this.stored;
  }

  async saveLocale(locale: Locale): Promise<void> {
    if (this.failNext) {
      this.failNext = false;
      throw new Error('simulated save failure');
    }
    this.stored = locale;
  }
}

/** One step in the generated sequence: a selection plus whether it should fail. */
interface Step {
  readonly locale: Locale;
  readonly fail: boolean;
}

const localeArb: fc.Arbitrary<Locale> = fc.constantFrom<Locale[]>(...LOCALES);

const stepArb: fc.Arbitrary<Step> = fc.record({
  locale: localeArb,
  fail: fc.boolean(),
});

describe('Feature: foundations, Property 22: A saved locale preference round-trips', () => {
  it('round-trips a successful save and leaves the previous preference unchanged on failure', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.option(localeArb, { nil: null }),
        fc.array(stepArb, { minLength: 1, maxLength: 20 }),
        async (initial, steps) => {
          const api = new InMemoryLocaleApi(initial);
          // Shadow model of what the store must hold after each step.
          let expectedStored: Locale | null = initial;

          for (const step of steps) {
            const previous = expectedStored;

            if (step.fail) {
              api.failNextSave();
              let raised: unknown;
              try {
                await saveLocalePreference(api, step.locale);
              } catch (err) {
                raised = err;
              }
              // Req 15.4: a save failure raises an error carrying the previous
              // locale and leaves the stored preference unchanged.
              expect(raised).toBeInstanceOf(LocalePreferenceSaveError);
              expect((raised as LocalePreferenceSaveError).previousLocale).toBe(previous);
              expect(await api.readLocale()).toBe(previous);
              // Shadow model is unchanged on failure.
            } else {
              const saved = await saveLocalePreference(api, step.locale);
              expect(saved).toBe(step.locale);
              expectedStored = step.locale;
              // Req 15.3: a successful save reads back exactly on the next
              // visit; resolveRenderedLocale renders the saved value regardless
              // of the fallback.
              const fallback: Locale = step.locale === 'fr' ? 'en' : 'fr';
              expect(await resolveRenderedLocale(api, fallback)).toBe(step.locale);
            }

            // Invariant after every step: the store matches the shadow model.
            expect(await api.readLocale()).toBe(expectedStored);
          }
        },
      ),
      { numRuns: 100 },
    );
  });
});
