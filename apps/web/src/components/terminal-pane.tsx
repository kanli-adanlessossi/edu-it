'use client';

// Interactive terminal for the Lab_View (hands-on-labs Req 13.3, 13.4, 15.2, 15.10,
// 16.4, 26.1).
//
// xterm.js and its fit addon are loaded with a client-only dynamic import, so
// nothing from them runs during server rendering. The pane forwards size changes
// to the gateway, reconnects with the bounded backoff derived from Config
// (`reconnectDelays`) while showing a live indication, and swallows the exit
// chord (Ctrl+M, Ctrl+Shift+M on macOS) so the learner can leave the terminal by
// keyboard. Enter is a different key and always reaches the shell.

import '@xterm/xterm/css/xterm.css';

import { useTranslations } from 'next-intl';
import { useEffect, useReducer, useRef, useState, type RefObject } from 'react';

import {
  initialLabViewState,
  labViewReducer,
  reconnectDelays,
  type LabClientConfig,
} from '../lab-state.js';
import {
  isExitChord,
  isMacPlatform,
  isReplaced,
  parseControlFrame,
  resizeFrame,
  shouldReconnect,
} from '../terminal-logic.js';

export interface TerminalPaneProps {
  /** Full `ws(s)://` URL of the session's terminal endpoint (see `terminalUrl`). */
  readonly url: string;
  /** Durations from the Config source; the retry schedule is derived from them. */
  readonly config: LabClientConfig;
  /** The "Check my work" control; receives focus when the exit chord is pressed. */
  readonly checkWorkRef: RefObject<HTMLElement | null>;
  /** Called with the gateway's `status` value (expired, ended, ...) before it closes. */
  readonly onSessionStatus?: (status: string) => void;
}

export function TerminalPane({ url, config, checkWorkRef, onSessionStatus }: TerminalPaneProps) {
  const t = useTranslations('Labs');
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [view, dispatch] = useReducer(labViewReducer, initialLabViewState);
  const [mac, setMac] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [justRestored, setJustRestored] = useState(false);
  const macRef = useRef(false);
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

      const delays = reconnectDelays(config);
      const encoder = new TextEncoder();
      let socket: WebSocket | null = null;
      let timer: ReturnType<typeof setTimeout> | undefined;
      let attempts = 0;
      let everLost = false;
      let finished = false;

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
        dispatch({ type: 'connection-lost' });
        if (attempts >= delays.length) {
          finished = true;
          dispatch({ type: 'reconnect-gave-up' });
          return;
        }
        const delay = delays[attempts] as number;
        attempts += 1;
        timer = setTimeout(() => {
          dispatch({ type: 'reconnect-attempted' });
          connect();
        }, delay);
      };

      const connect = () => {
        const ws = new WebSocket(url);
        ws.binaryType = 'arraybuffer';
        socket = ws;
        ws.onopen = () => {
          attempts = 0;
          setRestarting(false);
          if (everLost) {
            dispatch({ type: 'connection-restored' });
            setJustRestored(true);
          }
          refit();
          send(resizeFrame(term.rows, term.cols));
        };
        ws.onmessage = (message: MessageEvent) => {
          if (typeof message.data === 'string') {
            const frame = parseControlFrame(message.data);
            if (frame.type === 'ping') send('{"type":"pong"}');
            else if (frame.type === 'reconnect') setRestarting(true);
            else if (frame.type === 'status') onStatusRef.current?.(frame.status);
            return;
          }
          term.write(new Uint8Array(message.data as ArrayBuffer));
        };
        ws.onclose = (event: CloseEvent) => {
          if (socket !== ws) return;
          if (event.code === 1012) setRestarting(true);
          if (isReplaced(event.code) || !shouldReconnect(event.code)) {
            finished = true;
            return;
          }
          lose();
        };
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
  }, [url, config, checkWorkRef, t]);

  const shortcut = t(mac ? 'shortcut.mac' : 'shortcut.default');
  const hintId = 'terminal-pane-hint';
  let indication = '';
  if (view.phase === 'reconnecting') {
    indication = restarting ? t('reconnect.serverRestart') : t('reconnect.indicator');
  } else if (view.phase === 'error') {
    indication = t('reconnect.failed');
  } else if (justRestored) {
    indication = t('reconnect.restored');
  }

  return (
    <section aria-label={t('terminalLabel')} data-testid="terminal-pane">
      <div ref={containerRef} aria-describedby={hintId} data-testid="terminal-container" />
      <p id={hintId}>{t('shortcut.leaveTerminal', { shortcut })}</p>
      <div role="status" aria-live="polite" data-testid="terminal-connection">
        {indication}
        {view.phase === 'reconnecting' && view.attempt > 0 ? (
          <span>{t('reconnect.attempt', { attempt: view.attempt })}</span>
        ) : null}
      </div>
      {view.phase === 'error' ? (
        <button type="button" onClick={() => window.location.reload()}>
          {t('reconnect.reload')}
        </button>
      ) : null}
    </section>
  );
}
