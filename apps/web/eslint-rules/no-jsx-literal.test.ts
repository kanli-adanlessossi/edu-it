import { describe, expect, it } from 'vitest';
import { Linter, RuleTester } from 'eslint';
import tsParser from '@typescript-eslint/parser';

import { noJsxLiteralRule, eduItPlugin } from './no-jsx-literal.js';

// Unit fixtures proving the JSX-string-literal lint rule (Req 15.7). The rule
// must flag a user-facing string literal placed directly in JSX and report the
// offending file path and line/column, while leaving whitespace-only text and
// attribute string literals alone.
//
// We drive the rule with ESLint's flat-config RuleTester wired to the pinned
// typescript-eslint parser so TSX (JSX + TypeScript) fixtures parse the same
// way they do under the project's lint command. The parser produces the
// JSXText / JSXExpressionContainer nodes the rule listens for.

const ruleTester = new RuleTester({
  languageOptions: {
    parser: tsParser,
    ecmaVersion: 2022,
    sourceType: 'module',
    parserOptions: {
      ecmaFeatures: { jsx: true },
    },
  },
});

describe('no-jsx-literal rule via RuleTester (Req 15.7)', () => {
  it('accepts translation-key content and flags user-facing literals', () => {
    // RuleTester throws if any valid case reports or any invalid case does not
    // report exactly the expected errors, so a passing run is the assertion.
    ruleTester.run('no-jsx-literal', noJsxLiteralRule, {
      valid: [
        // Translation keys instead of bare literals: the intended pattern.
        { code: 'const A = () => <p>{t("greeting")}</p>;' },
        { code: 'const A = () => <p>{count}</p>;' },
        // Attribute string literals are structural, not user-facing text.
        { code: 'const A = () => <div className="box" id="main" />;' },
        { code: "const A = () => <img src={'/logo.png'} alt={t('logo')} />;" },
        // Whitespace-only JSXText between elements is formatting, not copy.
        { code: 'const A = () => (\n  <div>\n    <span>{t("k")}</span>\n  </div>\n);' },
        // An attribute whose value is a template literal is still an attribute.
        { code: 'const A = () => <div data-x={`static`} />;' },
      ],
      invalid: [
        // Bare text between tags.
        {
          code: 'const A = () => <p>Hello</p>;',
          errors: [{ messageId: 'jsxLiteral' }],
        },
        // String-literal expression container as element content.
        {
          code: "const A = () => <p>{'Hello'}</p>;",
          errors: [{ messageId: 'jsxLiteral' }],
        },
        // No-substitution template literal as element content.
        {
          code: 'const A = () => <p>{`Hello`}</p>;',
          errors: [{ messageId: 'jsxLiteral' }],
        },
        // Multiple offending literals each report once.
        {
          code: 'const A = () => (<div><p>Hello</p><span>World</span></div>);',
          errors: [{ messageId: 'jsxLiteral' }, { messageId: 'jsxLiteral' }],
        },
      ],
    });
  });
});

// Direct lint run asserting a report carries the file path and the line/column
// of the offending literal (Req 15.7 requires the reported location). The
// RuleTester above checks the message id; this uses the flat Linter so we can
// inspect the concrete location data a developer would see.
describe('no-jsx-literal report carries line and column (Req 15.7)', () => {
  const linter = new Linter();
  const config = {
    files: ['**/*.tsx'],
    languageOptions: {
      parser: tsParser,
      ecmaVersion: 2022 as const,
      sourceType: 'module' as const,
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    plugins: { 'edu-it': eduItPlugin },
    rules: { 'edu-it/no-jsx-literal': 'error' as const },
  };

  it('reports the line and column of a bare JSX literal', () => {
    const code = ['export const Page = () => (', '  <p>Bonjour</p>', ');', ''].join('\n');
    const messages = linter.verify(code, config, { filename: 'Page.tsx' });

    expect(messages).toHaveLength(1);
    const [message] = messages;
    expect(message.ruleId).toBe('edu-it/no-jsx-literal');
    // The literal "Bonjour" lives on the second line.
    expect(message.line).toBe(2);
    expect(message.column).toBeGreaterThan(0);
    expect(message.message).toContain('Bonjour');
  });

  it('does not flag whitespace-only text or attribute literals', () => {
    const code = [
      'export const Page = () => (',
      '  <section className="wrap" aria-label={label}>',
      '    <strong>{t("ok")}</strong>',
      '  </section>',
      ');',
      '',
    ].join('\n');
    const messages = linter.verify(code, config, { filename: 'Page.tsx' });
    expect(messages).toEqual([]);
  });
});
