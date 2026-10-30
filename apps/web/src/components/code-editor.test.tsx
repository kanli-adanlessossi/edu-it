// @vitest-environment jsdom
import { NextIntlClientProvider } from 'next-intl';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import en from '../messages/en.json';
import { CodeEditor } from './code-editor.js';

interface KeyLike {
  browserEvent: KeyboardEvent;
  preventDefault(): void;
  stopPropagation(): void;
}

// Hoisted fake Monaco: the real editor needs a browser layout engine and assets.
const monaco = vi.hoisted(() => ({
  keyDown: null as null | ((e: unknown) => void),
}));

vi.mock('@monaco-editor/loader', () => ({
  default: {
    config() {},
    init: async () => ({
      editor: {
        create: () => ({
          getValue: () => '',
          setValue() {},
          updateOptions() {},
          dispose() {},
          onDidChangeModelContent: () => ({ dispose() {} }),
          onKeyDown: (cb: (e: unknown) => void) => {
            monaco.keyDown = cb;
            return { dispose() {} };
          },
        }),
      },
    }),
  },
}));

let container: HTMLElement;
let root: Root;
const onExit = vi.fn();

async function mount() {
  await act(async () => {
    root.render(
      <NextIntlClientProvider locale="en" messages={en}>
        <CodeEditor
          files={[{ name: 'main.py', content: 'print(1)' }]}
          onChange={() => {}}
          onExit={onExit}
        />
      </NextIntlClientProvider>,
    );
  });
  await act(async () => {
    for (let i = 0; i < 10; i += 1) await Promise.resolve();
    await new Promise((r) => setTimeout(r, 0));
  });
}

function keyEvent(init: KeyboardEventInit & { key: string }): KeyLike {
  const browserEvent = new KeyboardEvent('keydown', { cancelable: true, ...init });
  return {
    browserEvent,
    preventDefault: vi.fn(() => browserEvent.preventDefault()),
    stopPropagation: vi.fn(),
  };
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  monaco.keyDown = null;
  onExit.mockClear();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

// Validates: Requirements 15.9, 15.10, 16.4
describe('CodeEditor keyboard access', () => {
  it('renders a labelled region per file with the exit shortcut documented', async () => {
    await mount();
    expect(container.querySelector('section[aria-label="Code editor"]')).not.toBeNull();
    expect(container.querySelector('[role="group"][aria-label="main.py"]')).not.toBeNull();
    expect(container.textContent).toContain('Press Ctrl+M to leave the editor');
    const host = container.querySelector('[aria-describedby="code-editor-hint"]');
    expect(host).not.toBeNull();
    expect(container.querySelector('#code-editor-hint')).not.toBeNull();
  });

  it('is reachable by Tab: nothing in the editor region removes it from tab order', async () => {
    await mount();
    expect(container.querySelector('[tabindex="-1"]')).toBeNull();
  });

  it('calls onExit and consumes the key on Ctrl+M so focus can leave', async () => {
    await mount();
    expect(monaco.keyDown).not.toBeNull();
    const e = keyEvent({ key: 'm', ctrlKey: true });
    monaco.keyDown?.(e);
    expect(onExit).toHaveBeenCalledTimes(1);
    expect(e.preventDefault).toHaveBeenCalled();
    expect(e.stopPropagation).toHaveBeenCalled();
  });

  it('does not trap or intercept Tab, Enter or plain m', async () => {
    await mount();
    for (const init of [{ key: 'Tab' }, { key: 'Enter' }, { key: 'm' }]) {
      const e = keyEvent(init);
      monaco.keyDown?.(e);
      expect(e.preventDefault).not.toHaveBeenCalled();
    }
    expect(onExit).not.toHaveBeenCalled();
  });
});
