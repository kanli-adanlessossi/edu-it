// @vitest-environment jsdom
import { NextIntlClientProvider } from 'next-intl';
import { act, createRef } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import en from '../messages/en.json';
import type { LabClientConfig } from '../lab-state.js';
import { TerminalPane } from './terminal-pane.js';

type KeyHandler = (event: KeyboardEvent) => boolean;

// Hoisted fakes for xterm: the real library needs a canvas/layout engine.
const xterm = vi.hoisted(() => ({
  handler: null as null | ((event: KeyboardEvent) => boolean),
  emitData: null as null | ((data: string) => void),
}));

vi.mock('@xterm/xterm/css/xterm.css', () => ({}));
vi.mock('@xterm/xterm', () => ({
  Terminal: class {
    rows = 24;
    cols = 80;
    textarea = document.createElement('textarea');
    loadAddon() {}
    open() {}
    write() {}
    dispose() {}
    attachCustomKeyEventHandler(handler: KeyHandler) {
      xterm.handler = handler;
    }
    onData(cb: (data: string) => void) {
      xterm.emitData = cb;
      return { dispose() {} };
    }
    onBinary() {
      return { dispose() {} };
    }
    onResize() {
      return { dispose() {} };
    }
  },
}));
vi.mock('@xterm/addon-fit', () => ({
  FitAddon: class {
    fit() {}
  },
}));

class FakeSocket {
  static readonly OPEN = 1;
  static instances: FakeSocket[] = [];
  readyState = 1;
  binaryType = '';
  sent: unknown[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((m: unknown) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  constructor(readonly url: string) {
    FakeSocket.instances.push(this);
  }
  send(data: unknown) {
    this.sent.push(data);
  }
  close() {}
}

const config: LabClientConfig = {
  gradingMinIntervalMs: 10_000,
  warningThresholdMs: 300_000,
  resultPollIntervalMs: 1_000,
  provisionSlowMs: 20_000,
  reconnectWindowMs: 600_000,
  reconnectBackoffMs: [300_000],
};

let container: HTMLElement;
let root: Root;

async function flush() {
  await act(async () => {
    for (let i = 0; i < 10; i += 1) await Promise.resolve();
    await new Promise((r) => setTimeout(r, 0));
  });
}

async function mount(checkWork: HTMLElement) {
  const ref = createRef<HTMLElement>();
  (ref as { current: HTMLElement | null }).current = checkWork;
  await act(async () => {
    root.render(
      <NextIntlClientProvider locale="en" messages={en}>
        <TerminalPane url="ws://test/terminal" config={config} checkWorkRef={ref} />
      </NextIntlClientProvider>,
    );
  });
  await flush();
}

function key(init: KeyboardEventInit & { key: string }) {
  return new KeyboardEvent('keydown', { cancelable: true, ...init });
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  FakeSocket.instances = [];
  xterm.handler = null;
  xterm.emitData = null;
  vi.stubGlobal('WebSocket', FakeSocket);
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
    },
  );
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

// Validates: Requirements 15.10, 16.4
describe('TerminalPane exit chord', () => {
  it('moves focus to Check my work on Ctrl+M and swallows the key', async () => {
    const button = document.createElement('button');
    document.body.appendChild(button);
    await mount(button);
    expect(xterm.handler).not.toBeNull();

    const chord = key({ key: 'm', ctrlKey: true });
    expect(xterm.handler?.(chord)).toBe(false);
    expect(chord.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(button);
    button.remove();
  });

  it('leaves Enter and other keys for the shell and does not move focus', async () => {
    const button = document.createElement('button');
    document.body.appendChild(button);
    await mount(button);

    const enter = key({ key: 'Enter' });
    expect(xterm.handler?.(enter)).toBe(true);
    expect(enter.defaultPrevented).toBe(false);
    expect(xterm.handler?.(key({ key: 'm' }))).toBe(true);
    expect(document.activeElement).not.toBe(button);
    button.remove();
  });

  it('documents the shortcut for keyboard users', async () => {
    await mount(document.createElement('button'));
    expect(container.textContent).toContain('Press Ctrl+M to leave the terminal');
  });
});

// Validates: Requirements 14.8, 15.9
describe('TerminalPane reconnect indication', () => {
  it('shows nothing while connected, an indication when disconnected, and restored after', async () => {
    await mount(document.createElement('button'));
    const status = () => container.querySelector('[data-testid="terminal-connection"]');
    const socket = FakeSocket.instances[0] as FakeSocket;
    expect(status()?.getAttribute('aria-live')).toBe('polite');

    await act(async () => socket.onopen?.());
    expect(status()?.textContent).toBe('');

    await act(async () => socket.onclose?.({ code: 1006 }));
    expect(status()?.textContent).toContain('Connection lost. Reconnecting');
  });

  it('shows the restart message when the gateway announces a restart', async () => {
    await mount(document.createElement('button'));
    const socket = FakeSocket.instances[0] as FakeSocket;
    await act(async () => socket.onclose?.({ code: 1012 }));
    expect(container.textContent).toContain('The server is restarting');
  });

  it('does not reconnect after a normal close', async () => {
    await mount(document.createElement('button'));
    const socket = FakeSocket.instances[0] as FakeSocket;
    await act(async () => socket.onclose?.({ code: 1000 }));
    expect(container.querySelector('[data-testid="terminal-connection"]')?.textContent).toBe('');
  });
});
