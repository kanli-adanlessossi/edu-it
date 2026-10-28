// Accessibility checks for every page built this phase (task 24.4, Req 17.5).
//
// Requirement 17.5: WHERE a page built in this phase is evaluated with axe-core,
// THE page SHALL report no serious or critical accessibility violation.
//
// Each foundational page is server-rendered to static HTML through the SAME
// React components a visitor sees — the application shell (skip link, banner,
// nav, language switcher, `<main>` landmark) wrapping the page body — under a
// real `NextIntlClientProvider` carrying the shipped FR/EN catalog, with the
// page's localized document `<title>` set exactly as `generateMetadata`
// produces it. The markup is loaded into a jsdom document and scanned with
// axe-core; the test fails if any violation is reported at the `serious` or
// `critical` impact level.
//
// The Next.js-only plumbing the client components touch (`next/link`,
// `next/navigation` hooks, the next-intl locale-aware `Link`) is stubbed so the
// components render outside the Next runtime. Those stubs affect only
// navigation wiring, never the accessibility-relevant markup (landmarks,
// labels, headings, roles, names).
//
// Full WCAG conformance also needs manual assistive-technology testing and
// expert review, which stays out of this automated scope (design: Accessibility
// testing). In particular, axe's color-contrast rule needs a rendering engine
// and so is not evaluated under jsdom; this scan covers the structural and
// semantic rules jsdom can evaluate.

import type { ReactElement, ReactNode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { JSDOM, VirtualConsole } from 'jsdom';
import { describe, expect, it, vi } from 'vitest';

// Stub the Next.js navigation surface the client components import, so the real
// page components render under `renderToStaticMarkup` without the Next runtime.
// `next/link` becomes a plain anchor (preserving `href`, `hrefLang`,
// `aria-label`, and children); the `next/navigation` hooks return stable
// values. None of this changes the markup axe-core evaluates.
vi.mock('next/link', () => ({
  __esModule: true,
  default: ({
    href,
    children,
    ...rest
  }: {
    href: string;
    children?: ReactNode;
  } & Record<string, unknown>) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock('next/navigation', () => ({
  __esModule: true,
  usePathname: () => '/fr',
  useSearchParams: () => new URLSearchParams(''),
  useRouter: () => ({
    push: () => undefined,
    replace: () => undefined,
    prefetch: () => undefined,
    back: () => undefined,
    forward: () => undefined,
    refresh: () => undefined,
  }),
  useParams: () => ({ locale: 'fr' }),
  redirect: () => undefined,
  notFound: () => undefined,
}));

// next-intl's `createNavigation` runs at import time and reaches into
// `next/navigation`; the locale-aware `Link` it produces is stubbed with a
// plain anchor so the page components that import it render outside the Next
// runtime. This preserves the href and children axe-core evaluates.
vi.mock('./i18n/navigation.js', () => ({
  __esModule: true,
  Link: ({
    href,
    children,
    ...rest
  }: {
    href: string;
    children?: ReactNode;
  } & Record<string, unknown>) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
  redirect: () => undefined,
  usePathname: () => '/fr',
  useRouter: () => ({ push: () => undefined, replace: () => undefined }),
  getPathname: () => '/fr',
}));

import axe from 'axe-core';
import { NextIntlClientProvider } from 'next-intl';

import { LOCALES, type Locale } from './locale.js';
import { MESSAGE_CATALOGS } from './messages/index.js';
import { AppShell } from './components/app-shell.js';
import { LanguageSwitcher } from './components/language-switcher.js';

// Page body components (the client halves rendered inside the shell's <main>).
import { SignUpForm } from './app/[locale]/sign-up/sign-up-form.js';
import { SignInForm } from './app/[locale]/sign-in/sign-in-form.js';
import { VerifyEmailResend } from './app/[locale]/verify-email/verify-email-resend.js';
import { ResetPasswordForm } from './app/[locale]/reset-password/reset-password-form.js';
import { LandingBody, DashboardBody } from './accessibility-page-bodies.js';

/** A `Meta` catalog key naming a page's localized document title. */
type MetaTitleKey = 'landing' | 'signUp' | 'signIn' | 'verifyEmail' | 'resetPassword' | 'dashboard';

/** A page built this phase, named for the scan report. */
interface PageUnderTest {
  readonly name: string;
  /** The `Meta` key for the localized document title `generateMetadata` sets. */
  readonly titleKey: MetaTitleKey;
  readonly body: (locale: Locale) => ReactElement;
}

// Every page built in task 24.1, each scanned under both `/fr` and `/en`. The
// `titleKey` mirrors the `generateMetadata` call each page makes.
const PAGES: readonly PageUnderTest[] = [
  { name: 'landing', titleKey: 'landing', body: (locale) => <LandingBody locale={locale} /> },
  { name: 'sign-up', titleKey: 'signUp', body: (locale) => <SignUpForm locale={locale} /> },
  { name: 'sign-in', titleKey: 'signIn', body: (locale) => <SignInForm locale={locale} /> },
  {
    name: 'verify-email',
    titleKey: 'verifyEmail',
    body: (locale) => <VerifyEmailResend locale={locale} />,
  },
  {
    name: 'reset-password',
    titleKey: 'resetPassword',
    body: (locale) => <ResetPasswordForm locale={locale} />,
  },
  { name: 'dashboard', titleKey: 'dashboard', body: () => <DashboardBody /> },
];

/** The localized document title for a page, read from the shipped `Meta` catalog. */
function pageTitle(locale: Locale, titleKey: MetaTitleKey): string {
  const meta = MESSAGE_CATALOGS[locale].Meta as Record<string, string>;
  return meta[titleKey];
}

/** Escape text destined for a static HTML `<title>`. */
function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/**
 * Render a full page document (shell + body) to static HTML for `locale`,
 * exactly as `[locale]/layout.tsx` wraps each page's content and each page's
 * `generateMetadata` sets its localized `<title>`.
 */
function renderPageHtml(page: PageUnderTest, locale: Locale): string {
  const markup = renderToStaticMarkup(
    <NextIntlClientProvider locale={locale} messages={MESSAGE_CATALOGS[locale]} timeZone="UTC">
      <AppShell switcher={<LanguageSwitcher />}>{page.body(locale)}</AppShell>
    </NextIntlClientProvider>,
  );
  const title = escapeHtml(pageTitle(locale, page.titleKey));
  // The shell renders the fragment that lives inside the layout's <body>; wrap
  // it in a complete document carrying the active language on <html> and the
  // page's localized <title>, matching what Next.js emits for the real page.
  return `<!DOCTYPE html><html lang="${locale}"><head><title>${title}</title></head><body>${markup}</body></html>`;
}

/** The impact levels requirement 17.5 forbids. */
const FORBIDDEN_IMPACTS = new Set(['serious', 'critical']);

interface AxeViolation {
  readonly id: string;
  readonly impact: string | null;
  readonly help: string;
  readonly nodes: ReadonlyArray<{ readonly html: string }>;
}

/**
 * Run axe-core against the given HTML in a fresh jsdom window and return only
 * the violations at a forbidden impact level (serious or critical).
 */
async function scanForSeriousOrCritical(html: string): Promise<AxeViolation[]> {
  // A quiet virtual console drops jsdom's "not implemented" canvas notice that
  // axe's (disabled) color-contrast probe would otherwise emit.
  const virtualConsole = new VirtualConsole();
  const dom = new JSDOM(html, { pretendToBeVisual: true, virtualConsole });
  const { window } = dom;

  // axe-core reads the ambient `window`/`document`; bind the jsdom ones for the
  // duration of this scan, then restore the host globals.
  const g = globalThis as Record<string, unknown>;
  const savedWindow = g.window;
  const savedDocument = g.document;
  const savedNavigator = Object.getOwnPropertyDescriptor(globalThis, 'navigator');

  g.window = window as unknown;
  g.document = window.document as unknown;
  try {
    Object.defineProperty(globalThis, 'navigator', { value: window.navigator, configurable: true });
  } catch {
    // A non-configurable host navigator is tolerated by axe's structural rules.
  }

  try {
    const results = await axe.run(window.document.documentElement, {
      resultTypes: ['violations'],
      // Color-contrast needs a layout/paint engine jsdom does not provide; the
      // remaining rules cover the structural and semantic a11y this scan targets.
      rules: { 'color-contrast': { enabled: false } },
    });
    return results.violations
      .filter((v) => FORBIDDEN_IMPACTS.has(v.impact ?? ''))
      .map((v) => ({
        id: v.id,
        impact: v.impact ?? null,
        help: v.help,
        nodes: v.nodes.map((n) => ({ html: n.html })),
      }));
  } finally {
    g.window = savedWindow;
    g.document = savedDocument;
    if (savedNavigator) {
      Object.defineProperty(globalThis, 'navigator', savedNavigator);
    }
    window.close();
  }
}

/** Format forbidden violations into a readable assertion message. */
function describeViolations(violations: AxeViolation[]): string {
  return violations
    .map(
      (v) =>
        `  [${v.impact}] ${v.id}: ${v.help}\n` + v.nodes.map((n) => `      ${n.html}`).join('\n'),
    )
    .join('\n');
}

describe('axe-core accessibility scan of every page built this phase (Req 17.5)', () => {
  for (const page of PAGES) {
    for (const locale of LOCALES) {
      it(`${page.name} (/${locale}) reports no serious or critical violation`, async () => {
        const html = renderPageHtml(page, locale);
        const violations = await scanForSeriousOrCritical(html);
        expect(
          violations,
          violations.length > 0
            ? `${page.name} (/${locale}) has forbidden axe-core violations:\n${describeViolations(violations)}`
            : undefined,
        ).toEqual([]);
      });
    }
  }
});
