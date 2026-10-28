// Placeholder privacy and terms pages served per the requested locale (Req 14.5).
//
// The full `/fr` and `/en` next-intl route trees and language switcher are
// wired by a later task; this module is the smallest stable primitive that
// task depends on: a pure, framework-free resolver that returns the correct
// FR or EN placeholder page content for a requested legal page and locale.
// Keeping the content here (rather than inline in a React component) lets the
// per-locale serving behavior be unit-tested now, and lets the later routing
// task render it under both route trees without duplicating copy.

import { type Locale } from './locale.js';

/** The legal placeholder pages served under both locale route trees. */
export const LEGAL_PAGES = ['privacy', 'terms'] as const;

export type LegalPage = (typeof LEGAL_PAGES)[number];

/** Returns true when `value` names a known legal page. */
export function isLegalPage(value: string): value is LegalPage {
  return (LEGAL_PAGES as readonly string[]).includes(value);
}

/** Rendered placeholder content for one legal page in one locale. */
export interface LegalPageContent {
  readonly page: LegalPage;
  readonly locale: Locale;
  /** Localized page title, suitable for the `<title>` and page heading. */
  readonly title: string;
  /** Localized placeholder body copy. */
  readonly body: string;
}

// Per-page, per-locale placeholder copy. FR and EN are both present for each
// page so the resolver can serve either locale (Req 14.5). This is intentional
// placeholder text, not the final legal wording.
const PLACEHOLDER_CONTENT: {
  readonly [P in LegalPage]: { readonly [L in Locale]: { title: string; body: string } };
} = {
  privacy: {
    fr: {
      title: 'Politique de confidentialité',
      body: 'Cette page de politique de confidentialité est un espace réservé. Le contenu définitif sera publié ultérieurement.',
    },
    en: {
      title: 'Privacy Policy',
      body: 'This privacy policy page is a placeholder. The final content will be published later.',
    },
  },
  terms: {
    fr: {
      title: "Conditions d'utilisation",
      body: "Cette page des conditions d'utilisation est un espace réservé. Le contenu définitif sera publié ultérieurement.",
    },
    en: {
      title: 'Terms of Service',
      body: 'This terms of service page is a placeholder. The final content will be published later.',
    },
  },
};

/**
 * Resolves the placeholder content for a legal page in the requested locale.
 *
 * Serving is driven entirely by the requested locale: the FR copy is returned
 * for `fr` and the EN copy for `en`, for both the privacy and terms pages
 * (Req 14.5).
 */
export function resolveLegalPage(page: LegalPage, locale: Locale): LegalPageContent {
  const { title, body } = PLACEHOLDER_CONTENT[page][locale];
  return { page, locale, title, body };
}
