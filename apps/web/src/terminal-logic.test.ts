import fc from 'fast-check';
import { describe, expect, it } from 'vitest';

import {
  isExitChord,
  isMacPlatform,
  parseControlFrame,
  resizeFrame,
  resolveWebSocketUrl,
  shouldReconnect,
  terminalUrl,
  type ChordKeyEvent,
} from './terminal-logic.js';

const key = (over: Partial<ChordKeyEvent>): ChordKeyEvent => ({
  key: 'm',
  ctrlKey: false,
  shiftKey: false,
  altKey: false,
  metaKey: false,
  ...over,
});

describe('exit chord', () => {
  it('is Ctrl+M off macOS and Ctrl+Shift+M on macOS', () => {
    expect(isExitChord(key({ ctrlKey: true }), false)).toBe(true);
    expect(isExitChord(key({ ctrlKey: true, shiftKey: true }), false)).toBe(false);
    expect(isExitChord(key({ ctrlKey: true, shiftKey: true, key: 'M' }), true)).toBe(true);
    expect(isExitChord(key({ ctrlKey: true }), true)).toBe(false);
  });

  it('never matches Enter or a plain M', () => {
    for (const mac of [false, true]) {
      expect(isExitChord(key({ key: 'Enter', ctrlKey: true }), mac)).toBe(false);
      expect(isExitChord(key({ key: 'Enter' }), mac)).toBe(false);
      expect(isExitChord(key({}), mac)).toBe(false);
    }
  });

  it('never matches any key other than m', () => {
    fc.assert(
      fc.property(
        fc.string({ minLength: 1 }).filter((k) => k.toLowerCase() !== 'm'),
        fc.boolean(),
        fc.boolean(),
        (k, shift, mac) => !isExitChord(key({ key: k, ctrlKey: true, shiftKey: shift }), mac),
      ),
    );
  });

  it('detects Apple platforms', () => {
    expect(isMacPlatform('MacIntel')).toBe(true);
    expect(isMacPlatform('Win32')).toBe(false);
  });
});

describe('connection helpers', () => {
  it('does not retry refusals or normal closes but retries drops', () => {
    for (const code of [1000, 4401, 4403, 4404]) expect(shouldReconnect(code)).toBe(false);
    for (const code of [1001, 1006, 1012, 4408]) expect(shouldReconnect(code)).toBe(true);
  });

  it('keeps a same-origin terminal URL relative and resolves it against the page', () => {
    const url = terminalUrl('/v1', 's1');
    expect(url).toBe('/v1/lab-sessions/s1/terminal');
    expect(resolveWebSocketUrl(url, { protocol: 'https:', host: 'edu-it.example:8443' })).toBe(
      'wss://edu-it.example:8443/v1/lab-sessions/s1/terminal',
    );
    expect(resolveWebSocketUrl(url, { protocol: 'http:', host: 'localhost:3000' })).toBe(
      'ws://localhost:3000/v1/lab-sessions/s1/terminal',
    );
    const absolute = 'wss://api.example.test/v1/lab-sessions/s1/terminal';
    expect(resolveWebSocketUrl(absolute, { protocol: 'http:', host: 'x' })).toBe(absolute);
  });

  it('builds the websocket URL and frames', () => {
    expect(terminalUrl('https://api.example.test/v1/', 'a b')).toBe(
      'wss://api.example.test/v1/lab-sessions/a%20b/terminal',
    );
    expect(JSON.parse(resizeFrame(24, 80))).toEqual({ type: 'resize', rows: 24, cols: 80 });
    expect(parseControlFrame('{"type":"status","status":"expired"}')).toEqual({
      type: 'status',
      status: 'expired',
    });
    expect(parseControlFrame('nope')).toEqual({ type: 'other' });
  });
});
