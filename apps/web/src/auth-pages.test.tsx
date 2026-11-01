// @vitest-environment jsdom
// Component tests for the sign-up, sign-in, and verify-email pages
// (api-route-wiring Req 14.5, 14.6, 14.8, 14.11, 17.6): the locale prefix on
// navigation and the verify-email success / invalid / resend states, in fr and en.

import { NextIntlClientProvider } from 'next-intl';
import type { ReactElement, ReactNode } from 'react';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { CompleteResetOutcome, VerifyEmailOutcome, VerifyingAuthApi } from './api-client.js';
import type { AuthPage } from './auth-forms.js';
import en from './messages/en.json';
import fr from './messages/fr.json';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const push = vi.fn();
vi.mock('next/navigation', () => ({
  __esModule: true,
  useRouter: () => ({ push }),
  usePathname: () => '/',
  useSearchParams: () => new URLSearchParams(''),
}));
vi.mock('./i18n/navigation.js', () => ({
  __esModule: true,
  Link: ({ href, children }: { href: string; children?: ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

const submit = vi.fn<VerifyingAuthApi['submit']>();
const verifyEmail = vi.fn<(token: string) => Promise<VerifyEmailOutcome>>();
const completePasswordReset =
  vi.fn<(token: string, newPassword: string) => Promise<CompleteResetOutcome>>();
const confirmGitHubLink = vi.fn<VerifyingAuthApi['confirmGitHubLink']>();
const declineGitHubLink = vi.fn<VerifyingAuthApi['declineGitHubLink']>();
vi.mock('./api-client.js', () => ({
  __esModule: true,
  createAuthApi: () => ({
    submit,
    verifyEmail,
    completePasswordReset,
    confirmGitHubLink,
    declineGitHubLink,
  }),
}));

const { SignUpForm } = await import('./app/[locale]/sign-up/sign-up-form.js');
const { SignInForm } = await import('./app/[locale]/sign-in/sign-in-form.js');
const { ResetPasswordForm } = await import('./app/[locale]/reset-password/reset-password-form.js');
const { GitHubConfirm } = await import('./app/[locale]/auth/github/confirm/github-confirm.js');
const { VerifyEmailResend } = await import('./app/[locale]/verify-email/verify-email-resend.js');

const CATALOGS = { en, fr } as const;
const LOCALES = ['fr', 'en'] as const;

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  push.mockReset();
  submit.mockReset();
  verifyEmail.mockReset();
  completePasswordReset.mockReset();
  confirmGitHubLink.mockReset();
  declineGitHubLink.mockReset();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

async function render(locale: 'fr' | 'en', element: ReactElement): Promise<void> {
  await act(async () => {
    root.render(
      <NextIntlClientProvider locale={locale} messages={CATALOGS[locale] as typeof en}>
        {element}
      </NextIntlClientProvider>,
    );
  });
}

async function fillAndSubmit(values: Record<string, string>): Promise<void> {
  for (const [name, value] of Object.entries(values)) {
    const input = container.querySelector<HTMLInputElement>(`input[name="${name}"]`)!;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
      setter.call(input, value);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
  }
  await act(async () => {
    container
      .querySelector('form')!
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  });
}

describe.each(LOCALES)('sign-up page (/%s)', (locale) => {
  it('submits to the sign-up page and navigates to /{locale}/verify-email', async () => {
    submit.mockResolvedValue({});
    await render(locale, <SignUpForm locale={locale} />);
    await fillAndSubmit({ email: 'a@b.test', password: 'long enough pass' });
    expect(submit).toHaveBeenCalledWith<[AuthPage, Record<string, string>]>('sign-up', {
      email: 'a@b.test',
      password: 'long enough pass',
    });
    expect(push).toHaveBeenCalledExactlyOnceWith(`/${locale}/verify-email`);
  });
});

describe.each(LOCALES)('sign-in page (/%s)', (locale) => {
  it('navigates to /{locale}{redirectTo}', async () => {
    submit.mockResolvedValue({ redirectTo: '/dashboard' });
    await render(locale, <SignInForm locale={locale} />);
    await fillAndSubmit({ email: 'a@b.test', password: 'pw' });
    expect(push).toHaveBeenCalledExactlyOnceWith(`/${locale}/dashboard`);
  });

  it('falls back to /{locale}/dashboard when no redirect is returned', async () => {
    submit.mockResolvedValue({});
    await render(locale, <SignInForm locale={locale} />);
    await fillAndSubmit({ email: 'a@b.test', password: 'pw' });
    expect(push).toHaveBeenCalledExactlyOnceWith(`/${locale}/dashboard`);
  });

  it('stays on the page and shows an alert when sign-in fails', async () => {
    submit.mockRejectedValue(new Error('nope'));
    await render(locale, <SignInForm locale={locale} />);
    await fillAndSubmit({ email: 'a@b.test', password: 'pw' });
    expect(push).not.toHaveBeenCalled();
    expect(container.querySelector('[role="alert"]')).not.toBeNull();
    expect(container.querySelector<HTMLInputElement>('input[name="password"]')!.value).toBe('');
    expect(container.querySelector<HTMLInputElement>('input[name="email"]')!.value).toBe(
      'a@b.test',
    );
  });
});

describe.each(LOCALES)('verify-email page (/%s)', (locale) => {
  const msgs = CATALOGS[locale].Auth.verifyEmail;

  it('without a token shows the instructions and the resend form without verifying', async () => {
    await render(locale, <VerifyEmailResend locale={locale} />);
    expect(verifyEmail).not.toHaveBeenCalled();
    expect(container.textContent).toContain(msgs.instructions);
    expect(container.querySelector('form')).not.toBeNull();
  });

  it('verifies the token once and shows success without the resend form', async () => {
    verifyEmail.mockResolvedValue('verified');
    await render(locale, <VerifyEmailResend locale={locale} token="tok-1" />);
    expect(verifyEmail).toHaveBeenCalledExactlyOnceWith('tok-1');
    expect(container.querySelector('h1')!.textContent).toBe(msgs.successTitle);
    expect(container.textContent).toContain(msgs.success);
    expect(container.querySelector('a[href="/sign-in"]')).not.toBeNull();
    expect(container.querySelector('form')).toBeNull();
  });

  it('shows the invalid-or-expired state together with the resend form', async () => {
    verifyEmail.mockResolvedValue('invalid');
    await render(locale, <VerifyEmailResend locale={locale} token="old" />);
    expect(container.querySelector('h1')!.textContent).toBe(msgs.invalidTitle);
    expect(container.querySelector('[role="alert"]')!.textContent).toBe(msgs.invalid);
    expect(container.querySelector('form')).not.toBeNull();
  });

  it('shows a generic verification error with the resend form when the call fails', async () => {
    verifyEmail.mockRejectedValue(new Error('network'));
    await render(locale, <VerifyEmailResend locale={locale} token="tok" />);
    expect(container.querySelector('[role="alert"]')!.textContent).toBe(msgs.verifyError);
    expect(container.querySelector('form')).not.toBeNull();
  });

  it('shows the resent confirmation after the resend form is accepted', async () => {
    verifyEmail.mockResolvedValue('invalid');
    submit.mockResolvedValue({});
    await render(locale, <VerifyEmailResend locale={locale} token="old" />);
    await fillAndSubmit({ email: 'a@b.test' });
    expect(submit).toHaveBeenCalledWith('verify-email', { email: 'a@b.test' });
    expect(container.querySelector('[role="status"]')!.textContent).toBe(msgs.resent);
  });
});

// ---- Milestone 2 (api-route-wiring Req 14.7, 14.9, 14.11, 14.12) -----------

describe.each(LOCALES)('sign-in GitHub link (/%s)', (locale) => {
  afterEach(() => vi.unstubAllEnvs());

  it.each(['false', '', 'TRUE', '1'])('is hidden when the flag is %j', async (value) => {
    vi.stubEnv('NEXT_PUBLIC_GITHUB_SIGN_IN_ENABLED', value);
    await render(locale, <SignInForm locale={locale} />);
    expect(container.querySelector('a[href$="/auth/github/authorize"]')).toBeNull();
  });

  it('is hidden when the flag is unset', async () => {
    vi.stubEnv('NEXT_PUBLIC_GITHUB_SIGN_IN_ENABLED', undefined);
    await render(locale, <SignInForm locale={locale} />);
    expect(container.querySelector('a[href$="/auth/github/authorize"]')).toBeNull();
  });

  it('links to the authorize route when the flag is "true"', async () => {
    vi.stubEnv('NEXT_PUBLIC_GITHUB_SIGN_IN_ENABLED', 'true');
    await render(locale, <SignInForm locale={locale} />);
    const link = container.querySelector('a[href$="/auth/github/authorize"]');
    expect(link).not.toBeNull();
    expect(link!.textContent).toBe(CATALOGS[locale].Auth.signIn.githubLink);
  });
});

describe.each(LOCALES)('reset-password page (/%s)', (locale) => {
  const msgs = CATALOGS[locale].Auth.resetPassword;

  it('without a token shows the request form and submits the email', async () => {
    submit.mockResolvedValue({});
    await render(locale, <ResetPasswordForm locale={locale} />);
    expect(container.querySelector('input[name="newPassword"]')).toBeNull();
    expect(container.textContent).toContain(msgs.requestInstructions);
    await fillAndSubmit({ email: 'a@b.test' });
    expect(submit).toHaveBeenCalledWith('reset-password', { email: 'a@b.test' });
    expect(completePasswordReset).not.toHaveBeenCalled();
  });

  it('with a token shows the new-password form and navigates to /{locale}{redirectTo}', async () => {
    completePasswordReset.mockResolvedValue({ status: 'completed', redirectTo: '/dashboard' });
    await render(locale, <ResetPasswordForm locale={locale} token="tok-1" />);
    expect(container.querySelector('input[name="email"]')).toBeNull();
    expect(container.textContent).toContain(msgs.setNewInstructions);
    await fillAndSubmit({ newPassword: 'a brand new passphrase' });
    expect(completePasswordReset).toHaveBeenCalledExactlyOnceWith(
      'tok-1',
      'a brand new passphrase',
    );
    expect(push).toHaveBeenCalledExactlyOnceWith(`/${locale}/dashboard`);
  });

  it('shows the invalid-link state with a link to request a new one', async () => {
    completePasswordReset.mockResolvedValue({ status: 'invalid' });
    await render(locale, <ResetPasswordForm locale={locale} token="old" />);
    await fillAndSubmit({ newPassword: 'a brand new passphrase' });
    expect(push).not.toHaveBeenCalled();
    expect(container.querySelector('h1')!.textContent).toBe(msgs.invalidTitle);
    expect(container.querySelector('[role="alert"]')!.textContent).toBe(msgs.invalid);
    expect(container.querySelector('a[href="/reset-password"]')!.textContent).toBe(msgs.requestNew);
    expect(container.querySelector('form')).toBeNull();
  });

  it('keeps the form and shows an alert when completion fails (for example a weak password)', async () => {
    completePasswordReset.mockRejectedValue(new Error('weak'));
    await render(locale, <ResetPasswordForm locale={locale} token="tok" />);
    await fillAndSubmit({ newPassword: 'short' });
    expect(push).not.toHaveBeenCalled();
    expect(container.querySelector('[role="alert"]')).not.toBeNull();
    expect(container.querySelector('form')).not.toBeNull();
  });
});

describe.each(LOCALES)('auth/github/confirm page (/%s)', (locale) => {
  const msgs = CATALOGS[locale].Auth.githubConfirm;

  function buttons(): HTMLButtonElement[] {
    return [...container.querySelectorAll<HTMLButtonElement>('button')];
  }

  async function click(label: string): Promise<void> {
    const button = buttons().find((b) => b.textContent === label)!;
    await act(async () => {
      button.click();
    });
  }

  it('offers confirm and decline', async () => {
    await render(locale, <GitHubConfirm locale={locale} />);
    expect(buttons().map((b) => b.textContent)).toEqual([msgs.confirm, msgs.decline]);
    expect(confirmGitHubLink).not.toHaveBeenCalled();
    expect(declineGitHubLink).not.toHaveBeenCalled();
  });

  it('confirm posts once and navigates to /{locale}{redirectTo}', async () => {
    confirmGitHubLink.mockResolvedValue({ redirectTo: '/dashboard' });
    await render(locale, <GitHubConfirm locale={locale} />);
    await click(msgs.confirm);
    expect(confirmGitHubLink).toHaveBeenCalledOnce();
    expect(declineGitHubLink).not.toHaveBeenCalled();
    expect(push).toHaveBeenCalledExactlyOnceWith(`/${locale}/dashboard`);
  });

  it('confirm falls back to /{locale}/dashboard without a redirect', async () => {
    confirmGitHubLink.mockResolvedValue({});
    await render(locale, <GitHubConfirm locale={locale} />);
    await click(msgs.confirm);
    expect(push).toHaveBeenCalledExactlyOnceWith(`/${locale}/dashboard`);
  });

  it('decline posts once and shows a neutral status without navigating', async () => {
    declineGitHubLink.mockResolvedValue(undefined);
    await render(locale, <GitHubConfirm locale={locale} />);
    await click(msgs.decline);
    expect(declineGitHubLink).toHaveBeenCalledOnce();
    expect(confirmGitHubLink).not.toHaveBeenCalled();
    expect(push).not.toHaveBeenCalled();
    expect(container.querySelector('[role="status"]')!.textContent).toBe(msgs.declined);
  });

  it('shows an alert and a way back when confirm fails', async () => {
    confirmGitHubLink.mockRejectedValue(new Error('expired'));
    await render(locale, <GitHubConfirm locale={locale} />);
    await click(msgs.confirm);
    expect(push).not.toHaveBeenCalled();
    expect(container.querySelector('[role="alert"]')!.textContent).toBe(msgs.error);
    expect(container.querySelector('a[href="/sign-in"]')).not.toBeNull();
  });
});
