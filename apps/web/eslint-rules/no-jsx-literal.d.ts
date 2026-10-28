// Type declarations for the custom JSX-literal ESLint rule module, which is
// authored in plain JS (with JSDoc) so ESLint can load it directly. These
// declarations let TypeScript consumers (the rule's own RuleTester tests in
// task 23.6, and the flat eslint config) import the rule and plugin with types
// instead of an implicit `any`. They describe the existing exports only; the
// implementation in `no-jsx-literal.js` is unchanged.

import type { Rule } from 'eslint';

/** The flat-config plugin exposing `edu-it/no-jsx-literal`. */
export declare const eduItPlugin: {
  rules: {
    'no-jsx-literal': Rule.RuleModule;
  };
};

/** The JSX-string-literal rule module (Req 15.7). */
export declare const noJsxLiteralRule: Rule.RuleModule;
