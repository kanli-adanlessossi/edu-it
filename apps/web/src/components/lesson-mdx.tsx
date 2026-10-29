// Lesson MDX renderer (ADR A5, Req 19.3).
//
// MDX is compiled and evaluated ONLY after the syntax tree passes the
// allowlist plugin, and only the allowlisted components (plus plain Markdown
// elements) are supplied to it. Anything else raises UnknownMdxComponentError.
import * as runtime from 'react/jsx-runtime';
import type { ComponentProps, ReactElement, ReactNode } from 'react';
import { evaluate } from '@mdx-js/mdx';

import { remarkMdxAllowlist } from '../mdx-allowlist.js';

interface ChildrenProps {
  children?: ReactNode;
}

function Callout({ children }: ChildrenProps) {
  return <aside data-component="Callout">{children}</aside>;
}

function Note({ children }: ChildrenProps) {
  return <aside data-component="Note">{children}</aside>;
}

function Warning({ children }: ChildrenProps) {
  return <aside data-component="Warning">{children}</aside>;
}

function CodeBlock({ children }: ChildrenProps) {
  return <div data-component="CodeBlock">{children}</div>;
}

function CommandOutput({ children }: ChildrenProps) {
  return (
    <pre data-component="CommandOutput">
      <code>{children}</code>
    </pre>
  );
}

function Figure({ children, caption }: ChildrenProps & { caption?: string }) {
  return (
    <figure data-component="Figure">
      {children}
      {caption ? <figcaption>{caption}</figcaption> : null}
    </figure>
  );
}

/** Static quiz reference: the interactive player lives on the quiz page. */
function Quiz({ children }: ChildrenProps) {
  return <div data-component="Quiz">{children}</div>;
}

/** Static, non-interactive terminal transcript (no lab or live shell). */
function Terminal({ children }: ChildrenProps) {
  return (
    <pre data-component="Terminal">
      <code>{children}</code>
    </pre>
  );
}

const SAFE_URL = /^(?:https?:|mailto:|\/|#|\.{0,2}\/)/i;

/** Only http(s), mailto, and relative/hash URLs survive; others become inert. */
export function sanitizeUrl(url: string | undefined): string | undefined {
  if (url === undefined) {
    return undefined;
  }
  const trimmed = url.trim();
  return SAFE_URL.test(trimmed) || !/^[a-z][a-z0-9+.-]*:/i.test(trimmed) ? trimmed : undefined;
}

function SafeAnchor(props: ComponentProps<'a'>) {
  return <a {...props} href={sanitizeUrl(props.href)} />;
}

function SafeImage(props: ComponentProps<'img'>) {
  const src = typeof props.src === 'string' ? sanitizeUrl(props.src) : undefined;
  return <img {...props} src={src} alt={props.alt ?? ''} />;
}

/** The only components made available to lesson MDX. */
export const lessonMdxComponents = {
  Callout,
  CodeBlock,
  CommandOutput,
  Figure,
  Note,
  Quiz,
  Terminal,
  Warning,
  a: SafeAnchor,
  img: SafeImage,
};

/**
 * Render lesson MDX source through the allowlist. Throws
 * UnknownMdxComponentError when the source references anything off-list.
 */
export async function renderLessonMdx(source: string): Promise<ReactElement> {
  const { default: Content } = await evaluate(source, {
    ...runtime,
    remarkPlugins: [remarkMdxAllowlist as never],
  });
  return <Content components={lessonMdxComponents} />;
}
