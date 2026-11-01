// Pure rules for the TerminalPane (hands-on-labs Req 13.3, 13.4, 15.10, 16.4).
//
// No DOM, no clock, no business numbers: the reconnect schedule comes from
// `reconnectDelays` (lab-state.ts), which derives it from the Config values.

/** The parts of a keyboard event the exit chord depends on. */
export interface ChordKeyEvent {
  readonly key: string;
  readonly ctrlKey: boolean;
  readonly shiftKey: boolean;
  readonly altKey: boolean;
  readonly metaKey: boolean;
}

/** True on Apple platforms, where the chord is Ctrl+Shift+M (Monaco's convention). */
export function isMacPlatform(platform: string): boolean {
  return /^(mac|iphone|ipad|ipod)/i.test(platform);
}

/**
 * The exit chord: Ctrl+M (Ctrl+Shift+M on macOS). Enter, which a terminal
 * encodes like Ctrl+M, is a different key and never matches.
 */
export function isExitChord(event: ChordKeyEvent, mac: boolean): boolean {
  if (event.key.toLowerCase() !== 'm') return false;
  if (!event.ctrlKey || event.altKey || event.metaKey) return false;
  return mac ? event.shiftKey : !event.shiftKey;
}

/** Close codes after which retrying is pointless (gateway refusals and normal end). */
const TERMINAL_CLOSE_CODES: ReadonlySet<number> = new Set([1000, 4401, 4403, 4404]);

/** Whether a closed socket should be retried with the bounded backoff. */
export function shouldReconnect(code: number): boolean {
  return !TERMINAL_CLOSE_CODES.has(code);
}

/** Close code 4409: another tab took over this session's terminal. Not retried either. */
export function isReplaced(code: number): boolean {
  return code === 4409;
}

/** `http(s)://host/v1` to the `ws(s)://host/v1/lab-sessions/{id}/terminal` URL. */
export function terminalUrl(apiBaseUrl: string, sessionId: string): string {
  const base = apiBaseUrl.replace(/\/+$/, '').replace(/^http/i, 'ws');
  return `${base}/lab-sessions/${encodeURIComponent(sessionId)}/terminal`;
}

/** Control frame sent for a terminal size change. */
export function resizeFrame(rows: number, cols: number): string {
  return JSON.stringify({ type: 'resize', rows, cols });
}

export type ControlFrame =
  | { readonly type: 'ping' }
  | { readonly type: 'reconnect' }
  | { readonly type: 'status'; readonly status: string }
  | { readonly type: 'other' };

/** Parse a text frame from the gateway; anything unrecognized is `other`. */
export function parseControlFrame(text: string): ControlFrame {
  try {
    const body = JSON.parse(text) as { type?: unknown; status?: unknown };
    if (body.type === 'ping') return { type: 'ping' };
    if (body.type === 'reconnect') return { type: 'reconnect' };
    if (body.type === 'status' && typeof body.status === 'string') {
      return { type: 'status', status: body.status };
    }
  } catch {
    // Not JSON: ignore.
  }
  return { type: 'other' };
}
