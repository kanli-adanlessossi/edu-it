import { NextIntlClientProvider } from 'next-intl';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import en from '../messages/en.json';
import fr from '../messages/fr.json';
import { ModuleList } from './module-list.js';

const modules = [
  { module_id: 'm2', ordinal: 2, state: 'locked', tested_out: false, completions: [] },
  { module_id: 'm1', ordinal: 1, state: 'unlocked', tested_out: false, completions: [] },
];

// Validates: Requirements 19.2
describe('ModuleList', () => {
  it.each([
    ['en', en],
    ['fr', fr],
  ] as const)('renders ordered modules with textual lock state and %s links', (locale, msgs) => {
    const html = renderToStaticMarkup(
      <NextIntlClientProvider locale={locale} messages={msgs as typeof en}>
        <ModuleList locale={locale} trackId="trk" modules={modules} />
      </NextIntlClientProvider>,
    );
    expect(html.indexOf('m1')).toBeLessThan(html.indexOf('m2'));
    expect(html).toContain('data-lock-state="unlocked"');
    expect(html).toContain('data-lock-state="locked"');
    expect(html).toContain(`/${locale}/tracks/trk/modules/m1`);
    expect(html).toContain(msgs.Learning.lock.locked);
    expect(html).toContain(msgs.Learning.lock.unlocked);
  });
});
