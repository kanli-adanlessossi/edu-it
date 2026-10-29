import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { MDX_COMPONENT_ALLOWLIST, UnknownMdxComponentError } from '../mdx-allowlist.js';
import { renderLessonMdx, sanitizeUrl } from './lesson-mdx.js';

async function html(source: string): Promise<string> {
  return renderToStaticMarkup(await renderLessonMdx(source));
}

// Validates: Requirements 19.3
describe('lesson MDX allowlist', () => {
  it('renders markdown and every allowlisted component', async () => {
    for (const name of MDX_COMPONENT_ALLOWLIST) {
      const out = await html(`# Title\n\n<${name}>hello</${name}>`);
      expect(out).toContain('<h1>Title</h1>');
      expect(out).toContain(`data-component="${name}"`);
    }
  });

  it.each([
    ['<Evil>x</Evil>', 'Evil'],
    ['<script>alert(1)</script>', 'script'],
    ['<a.b>x</a.b>', 'a.b'],
    ['import X from "x"\n\n# hi', 'import/export'],
    ['{process.env.SECRET}', 'expression'],
    ['<Note title={1 + 1}>x</Note>', 'Note attribute expression'],
    ['<Note {...props}>x</Note>', 'Note attribute expression'],
  ])('rejects %s', async (source, component) => {
    await expect(renderLessonMdx(source)).rejects.toMatchObject({
      name: 'UnknownMdxComponentError',
      component,
    });
    await expect(renderLessonMdx(source)).rejects.toBeInstanceOf(UnknownMdxComponentError);
  });

  it('neutralizes javascript: links', async () => {
    expect(sanitizeUrl('javascript:alert(1)')).toBeUndefined();
    expect(sanitizeUrl('https://example.com')).toBe('https://example.com');
    expect(sanitizeUrl('/fr/dashboard')).toBe('/fr/dashboard');
    expect(await html('[x](javascript:alert(1))')).not.toContain('javascript:');
  });
});
