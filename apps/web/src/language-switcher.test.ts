import { describe, expect, it } from 'vitest';
import { switchLocalePath, withLocalePath } from './language-switcher.js';

// Unit tests for the pure language-switcher URL algebra (Req 15.2). The
// numbered Property 21 test ("preserves path and query and round-trips") is a
// separate task sub-task; these examples pin the concrete behaviors it relies
// on: replacing the locale segment, preserving the path, query, and fragment,
// and round-tripping after two switches.

describe('withLocalePath', () => {
  it('replaces an existing locale segment, preserving the rest of the path', () => {
    expect(withLocalePath('/fr/dashboard', 'en')).toBe('/en/dashboard');
    expect(withLocalePath('/en/sign-in', 'fr')).toBe('/fr/sign-in');
  });

  it('prepends a locale segment when the path has none', () => {
    expect(withLocalePath('/dashboard', 'fr')).toBe('/fr/dashboard');
    expect(withLocalePath('dashboard', 'en')).toBe('/en/dashboard');
  });

  it('keeps the locale root as just the locale segment', () => {
    expect(withLocalePath('/fr', 'en')).toBe('/en');
    expect(withLocalePath('/', 'fr')).toBe('/fr');
  });

  it('preserves query parameters', () => {
    expect(withLocalePath('/fr/search?q=redis&page=2', 'en')).toBe('/en/search?q=redis&page=2');
  });

  it('preserves the fragment', () => {
    expect(withLocalePath('/fr/docs#section', 'en')).toBe('/en/docs#section');
    expect(withLocalePath('/fr/docs?a=1#section', 'en')).toBe('/en/docs?a=1#section');
  });
});

describe('switchLocalePath', () => {
  it('moves to the other-locale view of the current page', () => {
    expect(switchLocalePath('/fr/dashboard', 'fr')).toBe('/en/dashboard');
    expect(switchLocalePath('/en/dashboard', 'en')).toBe('/fr/dashboard');
  });

  it('uses the current locale as origin when the path carries none', () => {
    expect(switchLocalePath('/dashboard', 'fr')).toBe('/en/dashboard');
    expect(switchLocalePath('/dashboard', 'en')).toBe('/fr/dashboard');
  });

  it('preserves path and query while switching', () => {
    expect(switchLocalePath('/fr/search?q=redis&page=2', 'fr')).toBe('/en/search?q=redis&page=2');
  });

  it('round-trips: switching twice returns to the original URL', () => {
    const original = '/fr/search?q=redis&page=2#results';
    const once = switchLocalePath(original, 'fr');
    const twice = switchLocalePath(once, 'en');
    expect(twice).toBe(original);
  });
});
