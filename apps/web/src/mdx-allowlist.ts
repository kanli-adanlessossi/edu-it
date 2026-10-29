// The MDX component allowlist for the lesson reader (ADR A5, Req 19.3).
//
// `apps/web` owns the allowlist and performs the render. This set mirrors the
// validation-time `MDX_COMPONENT_ALLOWLIST` in `packages/content`; a component
// outside it is never rendered as an executable component.

/** The vetted component names lesson MDX may reference. */
export const MDX_COMPONENT_ALLOWLIST: ReadonlySet<string> = new Set([
  'Callout',
  'CodeBlock',
  'CommandOutput',
  'Figure',
  'Note',
  'Quiz',
  'Terminal',
  'Warning',
]);

/** True when `name` is an allowlisted MDX component. */
export function isAllowedMdxComponent(name: string): boolean {
  return MDX_COMPONENT_ALLOWLIST.has(name);
}

/** Raised when MDX references a component, expression, or import outside the allowlist. */
export class UnknownMdxComponentError extends Error {
  readonly component: string;

  constructor(component: string) {
    super(`MDX references a non-allowlisted component or construct: ${component}`);
    this.name = 'UnknownMdxComponentError';
    this.component = component;
  }
}

interface MdastNode {
  type: string;
  name?: string | null;
  attributes?: MdastAttribute[];
  children?: MdastNode[];
}

interface MdastAttribute {
  type: string;
  name?: string;
  value?: unknown;
}

/**
 * Remark plugin enforcing the allowlist on the MDX syntax tree. It rejects
 * every JSX element not on the allowlist (including lowercase/HTML-like and
 * member-expression names), ESM import/export, `{expression}` content, and
 * expression-valued or spread attributes, so no author-supplied code can run.
 */
export function remarkMdxAllowlist() {
  return (tree: MdastNode): void => {
    visit(tree);
  };
}

function visit(node: MdastNode): void {
  switch (node.type) {
    case 'mdxJsxFlowElement':
    case 'mdxJsxTextElement': {
      const name = node.name ?? '';
      // Fragments (`<>`) have a null name and are not allowlisted components.
      if (!isAllowedMdxComponent(name)) {
        throw new UnknownMdxComponentError(name === '' ? '<fragment>' : name);
      }
      for (const attribute of node.attributes ?? []) {
        if (attribute.type !== 'mdxJsxAttribute' || typeof attribute.value === 'object') {
          // Spread attributes and `{expression}` values carry code.
          if (attribute.value !== null && attribute.value !== undefined) {
            throw new UnknownMdxComponentError(`${name} attribute expression`);
          }
        }
      }
      break;
    }
    case 'mdxjsEsm':
      throw new UnknownMdxComponentError('import/export');
    case 'mdxFlowExpression':
    case 'mdxTextExpression':
      throw new UnknownMdxComponentError('expression');
    default:
      break;
  }
  for (const child of node.children ?? []) {
    visit(child);
  }
}
