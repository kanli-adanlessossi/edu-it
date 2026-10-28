import { describe, expect, it } from 'vitest';
import {
  LocalePreferenceSaveError,
  resolveRenderedLocale,
  saveLocalePreference,
  UnsupportedLocaleError,
  type LocalePreferenceApi,
} from './locale-preference.js';
import type { Locale } from './locale.js';

// Unit tests for the saved locale-preference round-trip (Req 15.3, 15.4). The
// numbered Property 22 test ("a saved locale preference round-trips") is a
// separate task sub-task; these examples pin the concrete success and
// failure-safety behaviors it relies on.

/** In-memory fake standing in for the generated API client's locale endpoint. */
class FakeLocaleApi implements LocalePreferenceApi {
  private stored: Locale | null;
  private failNext: boolean;

  constructor(initial: Locale | null = null) {
    this.stored = initial;
    this.failNext = false;
  }

  failOnce(): void {
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

describe('saveLocalePreference success (Req 15.3)', () => {
  it('persists the selection so it reads back on the next visit', async () => {
    const api = new FakeLocaleApi('fr');
    const saved = await saveLocalePreference(api, 'en');
    expect(saved).toBe('en');
    expect(await api.readLocale()).toBe('en');
  });

  it('rejects an unsupported locale without touching the API', async () => {
    const api = new FakeLocaleApi('fr');
    await expect(saveLocalePreference(api, 'de' as Locale)).rejects.toBeInstanceOf(
      UnsupportedLocaleError,
    );
    expect(await api.readLocale()).toBe('fr');
  });
});

describe('saveLocalePreference failure safety (Req 15.4)', () => {
  it('leaves the previous preference unchanged and returns an error', async () => {
    const api = new FakeLocaleApi('fr');
    api.failOnce();
    await expect(saveLocalePreference(api, 'en')).rejects.toBeInstanceOf(LocalePreferenceSaveError);
    // The previously saved preference is untouched.
    expect(await api.readLocale()).toBe('fr');
  });

  it('reports the previous locale on the raised error', async () => {
    const api = new FakeLocaleApi('fr');
    api.failOnce();
    try {
      await saveLocalePreference(api, 'en');
      throw new Error('expected a save failure');
    } catch (err) {
      expect(err).toBeInstanceOf(LocalePreferenceSaveError);
      expect((err as LocalePreferenceSaveError).previousLocale).toBe('fr');
    }
  });
});

describe('resolveRenderedLocale (Req 15.3)', () => {
  it('renders the saved preference when present', async () => {
    const api = new FakeLocaleApi('en');
    expect(await resolveRenderedLocale(api, 'fr')).toBe('en');
  });

  it('falls back when no preference is saved', async () => {
    const api = new FakeLocaleApi(null);
    expect(await resolveRenderedLocale(api, 'fr')).toBe('fr');
  });
});
