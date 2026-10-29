import { NextIntlClientProvider } from 'next-intl';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import en from '../messages/en.json';
import fr from '../messages/fr.json';
import { LabPlaceholder } from './lab-placeholder.js';

function render(locale: 'en' | 'fr', messages: typeof en) {
  return renderToStaticMarkup(
    <NextIntlClientProvider locale={locale} messages={messages}>
      <LabPlaceholder />
    </NextIntlClientProvider>,
  );
}

// Validates: Requirements 15.7, 19.7
describe('LabPlaceholder', () => {
  it.each([
    ['en', en, 'Coming soon'],
    ['fr', fr, 'Bientôt disponible'],
  ] as const)('shows only a coming-soon notice in %s, with no link', (locale, messages, text) => {
    const html = render(locale, messages as typeof en);
    expect(html).toContain(text);
    expect(html).not.toContain('<a');
    expect(html).not.toContain('href');
  });
});
