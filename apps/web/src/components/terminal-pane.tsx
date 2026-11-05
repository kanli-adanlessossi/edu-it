'use client';

// Interactive terminal for the Lab_View (hands-on-labs Req 13.3, 13.4, 15.2, 15.10,
// 16.4, 26.1; kubernetes-deployment Req 14.5, 14.6, 14.8, 20.1, 20.2).
//
// xterm.js and its fit addon are loaded with a client-only dynamic import, so
// nothing from them runs during server rendering. The pane forwards size changes
// to the gateway and reconnects with the named schedules of `reconnect-schedule.ts`:
// `drain` after a reconnect message, `restart` after an abrupt close. The indication
// is set in the same tick as the message or close. When a schedule ends the pane
// shows a translated error with a manual "Try again" action (the lab session is not
// touched); an ownership or expiry refusal shows a "sign in again" error instead.
// The exit chord (Ctrl+M, Ctrl+Shift+M on macOS) is swallowed so the learner can
// leave the terminal by keyboard. Enter is a different key and always reaches the shell.

import '@xterm/xterm/css/xterm.css';

import { useLocale, useTranslations } from 'next-intl';
import Link from 'next/link';
import { useEffect, useRef, useState, type RefObject } from 'react';

import { closeOutcome, reconnectSchedule, type ReconnectKind } from '../reconnect-schedule.js';
import { isExitChord, isMacPlatform, parseControlFrame, resizeFrame } from '../terminal-logic.js';

/** Connection state shown under the terminal. */
type Connection =
  | { readonly status: 'connected' }
  | { readonly status: 'reconnecting'; readonly attempt: number }
  | { readonly status: 'exhausted' }
  | { readonly status: 'reauth' };

export interface TerminalPaneProps {
  /** Full `ws(s)://` URL of the session's terminal endpoint (see `terminalUrl`). */
  readonly url: string;
  /** The "Check my work" control; receives focus when the exit chord is pressed. */
  readonly checkWorkRef: RefObject<HTMLElement | null>;
  /** Called with the gateway's `status` value (expired, ended, ...) before it closes. */
  readonly onSessionStatus?: (status: string) => void;
}

export function TerminalPane({ url, checkWorkRef, onSessionStatus }: TerminalPaneProps) {
  const t = useTranslations('Labs');
  const locale = useLocale();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [conn, setConn] = useState<Connection>({ status: 'connected' });
  const [mac, setMac] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [justRestored, setJustRestored] = useState(false);
  const macRef = useRef(false);
  const retryRef = useRef<(() => void) | null>(null);
  const onStatusRef = useRef(onSessionStatus);

  useEffect(() => {
    onStatusRef.current = onSessionStatus;
  }, [onSessionStatus]);

  useEffect(() => {
    const isMac = isMacPlatform(navigator.platform);
    macRef.current = isMac;
    setMac(isMac);
  }, []);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    let disposed = false;
    let cleanup: (() => void) | null = null;

    void (async () => {
      const [{ Terminal }, { FitAddon }] = await Promise.all([
        import('@xterm/xterm'),
        import('@xterm/addon-fit'),
      ]);
      if (disposed) return;

      const term = new Terminal({ screenReaderMode: true, cursorBlink: true });
      const fit = new FitAddon();
      term.loadAddon(fit);
      term.open(container);
      term.textarea?.setAttribute('aria-label', t('terminalLabel'));
      term.attachCustomKeyEventHandler((event) => {
        if (event.type === 'keydown' && isExitChord(event, macRef.current)) {
          event.preventDefault();
          checkWorkRef.current?.focus();
          return false; // swallowed: never sent to the shell
        }
        return true;
      });

      const encoder = new TextEncoder();
      let socket: WebSocket | null = null;
      let timer: ReturnType<typeof setTimeout> | undefined;
      let attempts = 0;
      let everLost = false;
      let finished = false;
      // `drain` once the server announced a restart (message or close 1012), else `restart`.
      let kind: ReconnectKind = 'restart';

      const send = (data: string | Uint8Array<ArrayBuffer>) => {
        if (socket?.readyState === WebSocket.OPEN) socket.send(data);
      };
      const refit = () => {
        try {
          fit.fit();
        } catch {
          // Container not measurable yet (hidden or zero-sized): skip this round.
        }
      };

      const lose = () => {
        if (finished || disposed) return;
        everLost = true;
        const delays = reconnectSchedule(kind);
        if (attempts >= delays.length) {
          finished = true;
          setConn({ status: 'exhausted' });
          return;
        }
        const delay = delays[attempts] as number;
        setConn({ status: 'reconnecting', attempt: attempts });
        attempts += 1;
        timer = setTimeout(() => {
          setConn({ status: 'reconnecting', attempt: attempts });
          connect();
        }, delay);
      };

      const connect = () => {
        const ws = new WebSocket(url);
        ws.binaryType = 'arraybuffer';
        socket = ws;
        ws.onopen = () => {
          attempts = 0;
          kind = 'restart';
          setRestarting(false);
          setConn({ status: 'connected' });
          if (everLost) setJustRestored(true);
          refit();
          send(resizeFrame(term.rows, term.cols));
        };
        ws.onmessage = (message: MessageEvent) => {
          if (typeof message.data === 'string') {
            const frame = parseControlFrame(message.data);
            if (frame.type === 'ping') send('{"type":"pong"}');
            else if (frame.type === 'reconnect') {
              // Show the indication now (Req 14.5, within 1 s); the close follows.
              kind = 'drain';
              everLost = true;
              setRestarting(true);
              setConn((current) =>
                current.status === 'reconnecting'
                  ? current
                  : { status: 'reconnecting', attempt: 0 },
              );
            } else if (frame.type === 'status') onStatusRef.current?.(frame.status);
            return;
          }
          term.write(new Uint8Array(message.data as ArrayBuffer));
        };
        ws.onclose = (event: CloseEvent) => {
          if (socket !== ws) return;
          if (event.code === 1012) {
            kind = 'drain';
            setRestarting(true);
          }
          const outcome = closeOutcome(event.code);
          if (outcome === 'reauth') {
            finished = true;
            setConn({ status: 'reauth' });
          } else if (outcome === 'stop') {
            finished = true;
          } else {
            lose();
          }
        };
      };

      // Manual retry: a fresh `restart` schedule; the lab session is not touched.
      retryRef.current = () => {
        if (disposed) return;
        clearTimeout(timer);
        finished = false;
        attempts = 0;
        kind = 'restart';
        setRestarting(false);
        setConn({ status: 'reconnecting', attempt: 0 });
        connect();
      };

      const dataSub = term.onData((data) => {
        setJustRestored(false);
        send(encoder.encode(data));
      });
      const binarySub = term.onBinary((data) => {
        send(Uint8Array.from(data, (c) => c.charCodeAt(0)));
      });
      const resizeSub = term.onResize(({ rows, cols }) => send(resizeFrame(rows, cols)));
      const observer = new ResizeObserver(refit);
      observer.observe(container);
      refit();
      connect();

      cleanup = () => {
        clearTimeout(timer);
        retryRef.current = null;
        observer.disconnect();
        dataSub.dispose();
        binarySub.dispose();
        resizeSub.dispose();
        const current = socket;
        socket = null;
        current?.close(1000);
        term.dispose();
      };
    })();

    return () => {
      disposed = true;
      cleanup?.();
    };
  }, [url, checkWorkRef, t]);

  const shortcut = t(mac ? 'shortcut.mac' : 'shortcut.default');
  const hintId = 'terminal-pane-hint';
  let indication = '';
  if (conn.status === 'reconnecting') {
    indication = restarting ? t('reconnect.serverRestart') : t('reconnect.indicator');
  } else if (conn.status === 'exhausted') {
    indication = t('reconnect.terminalFailed');
  } else if (conn.status === 'reauth') {
    indication = t('reconnect.reauthRequired');
  } else if (justRestored) {
    indication = t('reconnect.restored');
  }

  return (
    <section aria-label={t('terminalLabel')} data-testid="terminal-pane">
      <div ref={containerRef} aria-describedby={hintId} data-testid="terminal-container" />
      <p id={hintId}>{t('shortcut.leaveTerminal', { shortcut })}</p>
      <div role="status" aria-live="polite" data-testid="terminal-connection">
        {indication}
        {conn.status === 'reconnecting' && conn.attempt > 0 ? (
          <span>{t('reconnect.attempt', { attempt: conn.attempt })}</span>
        ) : null}
      </div>
      {conn.status === 'exhausted' ? (
        <button type="button" onClick={() => retryRef.current?.()}>
          {t('reconnect.retry')}
        </button>
      ) : null}
      {conn.status === 'reauth' ? (
        <Link href={`/${locale}/sign-in`}>{t('reconnect.signIn')}</Link>
      ) : null}
    </section>
  );
}
