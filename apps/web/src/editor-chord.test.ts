import { describe, expect, it } from 'vitest';
import { isExitChord, isMacPlatform, languageForFile } from './editor-chord.js';

const ev = (over: Partial<Parameters<typeof isExitChord>[0]>) => ({
  key: 'm',
  ctrlKey: true,
  shiftKey: false,
  altKey: false,
  metaKey: false,
  ...over,
});

describe('isExitChord', () => {
  it('matches Ctrl+M off macOS only', () => {
    expect(isExitChord(ev({}), false)).toBe(true);
    expect(isExitChord(ev({ shiftKey: true }), false)).toBe(false);
  });
  it('matches Ctrl+Shift+M on macOS only', () => {
    expect(isExitChord(ev({ key: 'M', shiftKey: true }), true)).toBe(true);
    expect(isExitChord(ev({}), true)).toBe(false);
  });
  it('ignores Enter, Tab, and other modified keys', () => {
    expect(isExitChord(ev({ key: 'Enter', ctrlKey: false }), false)).toBe(false);
    expect(isExitChord(ev({ key: 'Tab', ctrlKey: false }), false)).toBe(false);
    expect(isExitChord(ev({ ctrlKey: false }), false)).toBe(false);
    expect(isExitChord(ev({ altKey: true }), false)).toBe(false);
  });
});

describe('platform and language helpers', () => {
  it('detects Apple platforms', () => {
    expect(isMacPlatform('MacIntel')).toBe(true);
    expect(isMacPlatform('Win32')).toBe(false);
  });
  it('maps extensions with a plaintext fallback', () => {
    expect(languageForFile('main.py')).toBe('python');
    expect(languageForFile('run.SH')).toBe('shell');
    expect(languageForFile('Makefile')).toBe('plaintext');
  });
});
