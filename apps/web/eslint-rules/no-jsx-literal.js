// Custom ESLint rule: no user-facing string literal directly in JSX (Req 15.7).
//
// User-facing copy must come from a next-intl translation key, never a bare
// string literal embedded in JSX. When a JSX element contains a user-facing
// string literal instead of a translation key, this rule reports a violation;
// ESLint attaches the offending file path and line/column to every report, so
// `eslint .` exits non-zero and prints the file path and line of the literal
// (Req 15.7).
//
// The rule is a plain flat-config rule object (ESM) with no external
// dependency, so task 23.6 can exercise it directly with the pinned
// typescript-eslint RuleTester against fixtures.
//
// What counts as a "user-facing string literal in JSX":
//   - JSXText nodes whose trimmed content is non-empty (e.g. `<p>Hello</p>`).
//   - A string-literal JSX expression container used as element content
//     (e.g. `<p>{'Hello'}</p>` or `<p>{`Hello`}</p>`).
// What does NOT count (not user-facing text content):
//   - Whitespace-only JSXText (formatting between elements).
//   - String literals in attributes (e.g. `className="box"`, `id="main"`); the
//     rule targets rendered text content, not structural/style attributes.

/** @type {import('eslint').Rule.RuleModule} */
const rule = {
  meta: {
    type: 'problem',
    docs: {
      description:
        'disallow user-facing string literals directly in JSX; use a translation key instead',
    },
    schema: [],
    messages: {
      jsxLiteral:
        'User-facing string literal "{{ text }}" in JSX. Use a next-intl translation key instead of a bare literal.',
    },
  },
  create(context) {
    /** @param {string} value @returns {boolean} */
    function isUserFacingText(value) {
      return value.trim().length > 0;
    }

    return {
      // Bare text between JSX tags: <p>Hello</p>
      JSXText(node) {
        if (isUserFacingText(node.value)) {
          context.report({
            node,
            messageId: 'jsxLiteral',
            data: { text: node.value.trim() },
          });
        }
      },
      // String literal rendered as element content: <p>{'Hello'}</p>
      JSXExpressionContainer(node) {
        // Only element content containers have a JSXElement/JSXFragment parent;
        // attribute containers (e.g. id={'x'}) have a JSXAttribute parent and
        // are not rendered user-facing text, so they are left alone.
        const parentType = node.parent && node.parent.type;
        if (parentType !== 'JSXElement' && parentType !== 'JSXFragment') {
          return;
        }
        const expr = node.expression;
        if (
          expr.type === 'Literal' &&
          typeof expr.value === 'string' &&
          isUserFacingText(expr.value)
        ) {
          context.report({
            node: expr,
            messageId: 'jsxLiteral',
            data: { text: expr.value.trim() },
          });
          return;
        }
        // Template literal with no substitutions: <p>{`Hello`}</p>
        if (
          expr.type === 'TemplateLiteral' &&
          expr.expressions.length === 0 &&
          expr.quasis.length === 1 &&
          isUserFacingText(expr.quasis[0].value.cooked ?? '')
        ) {
          context.report({
            node: expr,
            messageId: 'jsxLiteral',
            data: { text: (expr.quasis[0].value.cooked ?? '').trim() },
          });
        }
      },
    };
  },
};

/**
 * The flat-config plugin that exposes the rule under the `edu-it` namespace as
 * `edu-it/no-jsx-literal`. Importing the plugin (rather than the bare rule)
 * keeps the rule id stable across the eslint config and the rule's own tests.
 */
export const eduItPlugin = {
  rules: {
    'no-jsx-literal': rule,
  },
};

export { rule as noJsxLiteralRule };
