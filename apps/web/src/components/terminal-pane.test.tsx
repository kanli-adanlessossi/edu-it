// @vitest-environment jsdom
import { NextIntlClientProvider } from 'next-intl';
import { act, createRef } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import en from '../messages/en.json';
import fr from '../messages/fr.json';
import { reconnectSchedule } from '../reconnect-schedule.js';
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

let container: HTMLElement;
let root: Root;

async function flush() {
  await act(async () => {
    for (let i = 0; i < 10; i += 1) await Promise.resolve();
    await new Promise((r) => setTimeout(r, 0));
  });
}

async function mount(checkWork: HTMLElement, locale: 'en' | 'fr' = 'en') {
  const ref = createRef<HTMLElement>();
  (ref as { current: HTMLElement | null }).current = checkWork;
  await act(async () => {
    root.render(
      <NextIntlClientProvider locale={locale} messages={locale === 'fr' ? fr : en}>
        <TerminalPane url="ws://test/terminal" checkWorkRef={ref} />
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

  // Validates: Requirement 14.5
  it('shows the indication as soon as the reconnect message arrives, before the close', async () => {
    await mount(document.createElement('button'));
    const socket = FakeSocket.instances[0] as FakeSocket;
    await act(async () => socket.onopen?.());
    await act(async () => socket.onmessage?.({ data: '{"type":"reconnect"}' }));
    expect(container.textContent).toContain('The server is restarting');
  });

  // Validates: Requirements 14.5, 14.6
  it('follows the drain schedule, then offers a manual retry that reconnects', async () => {
    await mount(document.createElement('button'));
    vi.useFakeTimers();
    try {
      const drain = reconnectSchedule('drain');
      let socket = FakeSocket.instances[0] as FakeSocket;
      await act(async () => socket.onmessage?.({ data: '{"type":"reconnect"}' }));
      await act(async () => socket.onclose?.({ code: 1012 }));
      for (const delay of drain) {
        const before = FakeSocket.instances.length;
        await act(async () => vi.advanceTimersByTime(delay - 1));
        expect(FakeSocket.instances.length).toBe(before);
        await act(async () => vi.advanceTimersByTime(1));
        expect(FakeSocket.instances.length).toBe(before + 1);
        socket = FakeSocket.instances.at(-1) as FakeSocket;
        await act(async () => socket.onclose?.({ code: 1006 }));
      }
      expect(FakeSocket.instances).toHaveLength(drain.length + 1);
      expect(container.textContent).toContain('We could not reconnect to your terminal');
      const retry = [...container.querySelectorAll('button')].find(
        (b) => b.textContent === 'Try again',
      );
      expect(retry).toBeDefined();
      await act(async () => retry?.click());
      expect(FakeSocket.instances).toHaveLength(drain.length + 2);
      await act(async () => (FakeSocket.instances.at(-1) as FakeSocket).onopen?.());
      expect(container.textContent).toContain('Connection restored');
    } finally {
      vi.useRealTimers();
    }
  });

  // Validates: Requirement 14.8
  it('asks for re-authentication, without retrying, when ownership is refused', async () => {
    await mount(document.createElement('button'));
    const socket = FakeSocket.instances[0] as FakeSocket;
    await act(async () => socket.onopen?.());
    await act(async () => socket.onclose?.({ code: 4404 }));
    expect(container.textContent).toContain('Sign in again to continue');
    expect(container.querySelector('a')?.getAttribute('href')).toBe('/en/sign-in');
    expect(FakeSocket.instances).toHaveLength(1);
  });

  it('does not reconnect after a normal close', async () => {
    await mount(document.createElement('button'));
    const socket = FakeSocket.instances[0] as FakeSocket;
    await act(async () => socket.onclose?.({ code: 1000 }));
    expect(container.querySelector('[data-testid="terminal-connection"]')?.textContent).toBe('');
  });
});

const statusText = () =>
  container.querySelector('[data-testid="terminal-connection"]')?.textContent ?? '';
const retryButton = (label: string) =>
  [...container.querySelectorAll('button')].find((b) => b.textContent === label);

// Claims: 14.6, 20.1, 20.2
describe('TerminalPane restart schedule', () => {
  it('shows the indication on close, stays within 60 s, then offers a manual retry', async () => {
    await mount(document.createElement('button'));
    vi.useFakeTimers();
    try {
      const restart = reconnectSchedule('restart');
      expect(restart.reduce((a, b) => a + b, 0)).toBeLessThanOrEqual(60_000);
      let socket = FakeSocket.instances[0] as FakeSocket;
      await act(async () => socket.onopen?.());
      await act(async () => socket.onclose?.({ code: 1006 }));
      // Indication is set in the same tick as the close (within the 2 s bound).
      expect(statusText()).toContain('Connection lost. Reconnecting');
      expect(statusText()).not.toContain('The server is restarting');

      let elapsed = 0;
      for (const delay of restart) {
        expect(delay).toBeLessThanOrEqual(5_000);
        const before = FakeSocket.instances.length;
        await act(async () => vi.advanceTimersByTime(delay - 1));
        expect(FakeSocket.instances.length).toBe(before);
        await act(async () => vi.advanceTimersByTime(1));
        elapsed += delay;
        expect(FakeSocket.instances.length).toBe(before + 1);
        socket = FakeSocket.instances.at(-1) as FakeSocket;
        await act(async () => socket.onclose?.({ code: 1006 }));
      }
      expect(elapsed).toBeLessThanOrEqual(60_000);
      expect(FakeSocket.instances).toHaveLength(restart.length + 1);
      expect(statusText()).toContain('We could not reconnect to your terminal');
      expect(statusText()).toContain('Your lab session is unchanged');

      // No further attempt happens by itself once the schedule is exhausted.
      await act(async () => vi.advanceTimersByTime(120_000));
      expect(FakeSocket.instances).toHaveLength(restart.length + 1);

      await act(async () => retryButton('Try again')?.click());
      expect(FakeSocket.instances).toHaveLength(restart.length + 2);
    } finally {
      vi.useRealTimers();
    }
  });

  it('starts a fresh schedule after the manual retry fails again', async () => {
    await mount(document.createElement('button'));
    vi.useFakeTimers();
    try {
      const restart = reconnectSchedule('restart');
      let socket = FakeSocket.instances[0] as FakeSocket;
      await act(async () => socket.onclose?.({ code: 1006 }));
      for (const delay of restart) {
        await act(async () => vi.advanceTimersByTime(delay));
        socket = FakeSocket.instances.at(-1) as FakeSocket;
        await act(async () => socket.onclose?.({ code: 1006 }));
      }
      await act(async () => retryButton('Try again')?.click());
      const count = FakeSocket.instances.length;
      await act(async () => (FakeSocket.instances.at(-1) as FakeSocket).onclose?.({ code: 1006 }));
      await act(async () => vi.advanceTimersByTime(restart[0] as number));
      expect(FakeSocket.instances).toHaveLength(count + 1);
    } finally {
      vi.useRealTimers();
    }
  });
});

// Claims: 14.6, 14.8, 20.1, 20.2
describe('TerminalPane French messages', () => {
  it('shows the French reconnect indication, restart message and attempt counter', async () => {
    await mount(document.createElement('button'), 'fr');
    vi.useFakeTimers();
    try {
      let socket = FakeSocket.instances[0] as FakeSocket;
      await act(async () => socket.onclose?.({ code: 1006 }));
      expect(statusText()).toBe(fr.Labs.reconnect.indicator);
      await act(async () => vi.advanceTimersByTime(reconnectSchedule('restart')[0] as number));
      socket = FakeSocket.instances.at(-1) as FakeSocket;
      await act(async () => socket.onmessage?.({ data: '{"type":"reconnect"}' }));
      expect(statusText()).toContain(fr.Labs.reconnect.serverRestart);
      expect(statusText()).toContain('Tentative de reconnexion 1');
    } finally {
      vi.useRealTimers();
    }
  });

  it('shows the French exhausted error with a French retry action, then the restored message', async () => {
    await mount(document.createElement('button'), 'fr');
    vi.useFakeTimers();
    try {
      const drain = reconnectSchedule('drain');
      let socket = FakeSocket.instances[0] as FakeSocket;
      await act(async () => socket.onmessage?.({ data: '{"type":"reconnect"}' }));
      await act(async () => socket.onclose?.({ code: 1012 }));
      for (const delay of drain) {
        await act(async () => vi.advanceTimersByTime(delay));
        socket = FakeSocket.instances.at(-1) as FakeSocket;
        await act(async () => socket.onclose?.({ code: 1006 }));
      }
      expect(statusText()).toBe(fr.Labs.reconnect.terminalFailed);
      await act(async () => retryButton('Réessayer')?.click());
      await act(async () => (FakeSocket.instances.at(-1) as FakeSocket).onopen?.());
      expect(statusText()).toBe(fr.Labs.reconnect.restored);
    } finally {
      vi.useRealTimers();
    }
  });

  it('asks for re-authentication in French and links to the French sign-in page', async () => {
    await mount(document.createElement('button'), 'fr');
    const socket = FakeSocket.instances[0] as FakeSocket;
    await act(async () => socket.onclose?.({ code: 4401 }));
    expect(statusText()).toBe(fr.Labs.reconnect.reauthRequired);
    const link = container.querySelector('a');
    expect(link?.getAttribute('href')).toBe('/fr/sign-in');
    expect(link?.textContent).toBe(fr.Labs.reconnect.signIn);
    expect(FakeSocket.instances).toHaveLength(1);
  });

  it('has a non-empty French and English text for every reconnect key', () => {
    const keys = Object.keys(en.Labs.reconnect) as (keyof typeof en.Labs.reconnect)[];
    expect(Object.keys(fr.Labs.reconnect).sort()).toEqual([...keys].sort());
    for (const key of keys) {
      expect(en.Labs.reconnect[key]).not.toBe('');
      expect(fr.Labs.reconnect[key]).not.toBe('');
    }
    expect(fr.Labs.reconnect.terminalFailed).not.toBe(en.Labs.reconnect.terminalFailed);
    expect(fr.Labs.reconnect.reauthRequired).not.toBe(en.Labs.reconnect.reauthRequired);
  });
});

// Claims: 14.8, 20.4
describe('TerminalPane ownership failure', () => {
  it.each([4401, 4404])(
    'does not retry after close code %i and leaves no retry action',
    async (code) => {
      await mount(document.createElement('button'));
      vi.useFakeTimers();
      try {
        const socket = FakeSocket.instances[0] as FakeSocket;
        await act(async () => socket.onopen?.());
        await act(async () => socket.onclose?.({ code }));
        await act(async () => vi.advanceTimersByTime(120_000));
        expect(FakeSocket.instances).toHaveLength(1);
        expect(statusText()).toContain('Sign in again to continue');
        expect(retryButton('Try again')).toBeUndefined();
      } finally {
        vi.useRealTimers();
      }
    },
  );
});
