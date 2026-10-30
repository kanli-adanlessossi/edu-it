// Pure helpers for the code editor: the documented keyboard exit chord and the
// file-name -> Monaco language mapping (Req 15.9, 15.10, 16.4). Kept free of
// Monaco and React so it is unit-testable.

export interface ChordKeyEvent {
  readonly key: string;
  readonly ctrlKey: boolean;
  readonly shiftKey: boolean;
  readonly altKey: boolean;
  readonly metaKey: boolean;
}

/** True on Apple platforms, where the exit chord is Ctrl+Shift+M. */
export function isMacPlatform(platform: string): boolean {
  return /^(mac|iphone|ipad|ipod)/i.test(platform);
}

/**
 * True when the event is the documented exit chord: Ctrl+M (Ctrl+Shift+M on
 * macOS). Any other modifier combination is not the chord, so plain typing,
 * Enter and Tab are never intercepted.
 */
export function isExitChord(event: ChordKeyEvent, mac: boolean): boolean {
  if (event.key.toLowerCase() !== 'm') return false;
  if (!event.ctrlKey || event.altKey || event.metaKey) return false;
  return mac ? event.shiftKey : !event.shiftKey;
}

const LANGUAGE_BY_EXTENSION: Readonly<Record<string, string>> = {
  py: 'python',
  sh: 'shell',
  bash: 'shell',
  js: 'javascript',
  ts: 'typescript',
  json: 'json',
  yml: 'yaml',
  yaml: 'yaml',
  md: 'markdown',
  html: 'html',
  css: 'css',
  sql: 'sql',
  go: 'go',
  rs: 'rust',
  java: 'java',
  c: 'c',
  cpp: 'cpp',
};

/** Monaco language id for a learner file name; plain text when unknown. */
export function languageForFile(name: string): string {
  const dot = name.lastIndexOf('.');
  if (dot < 0) return 'plaintext';
  return LANGUAGE_BY_EXTENSION[name.slice(dot + 1).toLowerCase()] ?? 'plaintext';
}
