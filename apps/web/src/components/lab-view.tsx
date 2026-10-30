'use client';

// Lab_View (hands-on-labs Req 15.1-15.12, 16.2-16.4, 18.1, 18.2, 18.5, 25.4, 26.4).
//
// One page with the lab's tasks in the active locale, a terminal (xterm.js) or a
// code editor (Monaco) by lab kind, progressive hints, "Check my work" with the
// 10 s rule, a confirmed "Reset lab", a TTL / idle timer with a warning, results
// streamed incrementally over SSE, and clear messages for slow provisioning,
// a failed start, and an ended session.
//
// All rules live in lab-state.ts (pure, tested); this file only wires them to
// the API and the DOM. Server time comes from the API's `server_now` offset, so
// the client clock never decides the 10 s rule or the timer. Live regions
// (`role="status"`, polite) carry results, timer warnings, and reconnect status;
// the timer itself is not announced every second.
//
// "Check my work" uses `aria-disabled` rather than `disabled` so it stays
// focusable: the documented exit shortcut moves focus to it from the terminal and
// the editor, and the reason it is unavailable is read through `aria-describedby`.

import './lab-view.css';

import { useTranslations } from 'next-intl';
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';

import {
  LabApiError,
  fetchLabSession,
  findActiveSession,
  isActiveStatus,
  labApiBaseUrl,
  labClientConfig,
  requestGrading,
  resetLabSession,
  revealHint,
  saveLabFiles,
  startLabSession,
  streamRunEvents,
  type LabDetailView,
  type LabSessionView,
} from '../lab-api.js';
import {
  canCheckWork,
  initialLabViewState,
  labViewReducer,
  reconnectDelays,
  serverTimeMs,
  warningActive,
  type CheckResultView,
  type LabViewState,
} from '../lab-state.js';
import { terminalUrl } from '../terminal-logic.js';
import { CodeEditor } from './code-editor.js';
import { TerminalPane } from './terminal-pane.js';

/** How often the session is re-read (status, deadlines, activity); a UI cadence, not a business rule. */
const SESSION_POLL_MS = 2000;
/** How long after the last edit the editor files are saved (this also counts as activity). */
const FILE_SAVE_DEBOUNCE_MS = 1500;

const config = labClientConfig;

function newKey(): string {
  return globalThis.crypto.randomUUID();
}

function formatClock(ms: number): { minutes: string; seconds: string } {
  const total = Math.ceil(ms / 1000);
  return {
    minutes: String(Math.floor(total / 60)),
    seconds: String(total % 60).padStart(2, '0'),
  };
}

export interface LabViewProps {
  readonly locale: string;
  readonly lab: LabDetailView;
}

export function LabView({ locale, lab }: LabViewProps) {
  const t = useTranslations('Labs');
  const [session, setSession] = useState<LabSessionView | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [gone, setGone] = useState(false);

  const describe = useCallback(
    (cause: unknown): string =>
      cause instanceof LabApiError && cause.message !== '' ? cause.message : t('errors.generic'),
    [t],
  );

  useEffect(() => {
    let cancelled = false;
    findActiveSession(lab.labId, locale)
      .then((found) => {
        if (!cancelled) setSession(found);
      })
      .catch((cause: unknown) => {
        if (!cancelled) setError(describe(cause));
      })
      .finally(() => {
        if (!cancelled) setLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, [lab.labId, locale, describe]);

  const sessionId = session !== null && isActiveStatus(session.status) ? session.stableId : null;
  const pollNow = useCallback(async () => {
    if (sessionId === null) return;
    try {
      const next = await fetchLabSession(sessionId, locale);
      setSession(next);
    } catch (cause) {
      if (cause instanceof LabApiError && cause.status === 404) setGone(true);
    }
  }, [sessionId, locale]);

  useEffect(() => {
    if (sessionId === null) return;
    const timer = setInterval(() => void pollNow(), SESSION_POLL_MS);
    return () => clearInterval(timer);
  }, [sessionId, pollNow]);

  const start = useCallback(async () => {
    setBusy(true);
    setError(null);
    setGone(false);
    try {
      setSession(await startLabSession(lab.labId, locale, newKey()));
    } catch (cause) {
      setError(describe(cause));
    } finally {
      setBusy(false);
    }
  }, [lab.labId, locale, describe]);

  const active = session !== null && isActiveStatus(session.status) && !gone;

  return (
    <div className="lab-view" lang={locale}>
      <h1>{lab.title}</h1>
      {error !== null ? <p role="alert">{error}</p> : null}
      {!loaded ? <p role="status">{t('status.loading')}</p> : null}
      {loaded && !active ? (
        <>
          <TaskList lab={lab} locale={locale} sessionId={null} results={[]} />
          <SessionOutcome
            session={gone ? null : session}
            gone={gone}
            busy={busy}
            onStart={() => void start()}
          />
        </>
      ) : null}
      {loaded && active ? (
        <ActiveLab
          key={session.stableId}
          lab={lab}
          locale={locale}
          session={session}
          onPollNow={() => void pollNow()}
          onReplaced={setSession}
          describe={describe}
        />
      ) : null}
    </div>
  );
}

// --- no active session: start, failed, or ended -------------------------------------

interface SessionOutcomeProps {
  readonly session: LabSessionView | null;
  readonly gone: boolean;
  readonly busy: boolean;
  readonly onStart: () => void;
}

function SessionOutcome({ session, gone, busy, onStart }: SessionOutcomeProps) {
  const t = useTranslations('Labs');

  if (session === null && !gone) {
    return (
      <p>
        <button type="button" onClick={onStart} disabled={busy}>
          {t('startLab')}
        </button>
      </p>
    );
  }
  if (session?.status === 'failed') {
    return (
      <section aria-labelledby="lab-failed-title">
        <h2 id="lab-failed-title">{t('provisioning.failedTitle')}</h2>
        <p role="alert">{session.error?.detail || t('provisioning.failedBody')}</p>
        <button type="button" onClick={onStart} disabled={busy}>
          {t('provisioning.retry')}
        </button>
      </section>
    );
  }
  const body = gone
    ? t('errors.sessionNotFound')
    : session?.status === 'expired'
      ? t('session.expiredBody')
      : session?.status === 'idle-expired'
        ? t('session.idleBody')
        : t('session.endedBody');
  return (
    <section aria-labelledby="lab-ended-title">
      <h2 id="lab-ended-title">{t('session.endedTitle')}</h2>
      <p role="status">{body}</p>
      <button type="button" onClick={onStart} disabled={busy}>
        {t('session.startNew')}
      </button>
    </section>
  );
}

// --- tasks and hints ------------------------------------------------------------------

interface TaskListProps {
  readonly lab: LabDetailView;
  readonly locale: string;
  readonly sessionId: string | null;
  readonly results: readonly CheckResultView[];
}

function TaskList({ lab, locale, sessionId, results }: TaskListProps) {
  const t = useTranslations('Labs');
  const [hints, setHints] = useState<Readonly<Record<string, readonly string[]>>>({});
  const [hintError, setHintError] = useState(false);
  const hasHints = lab.tasks.some((task) => task.hintCount > 0);

  async function reveal(taskId: string, level: number) {
    if (sessionId === null) return;
    setHintError(false);
    try {
      const hint = await revealHint(sessionId, taskId, level, locale);
      setHints((prev) => {
        const known = [...(prev[taskId] ?? [])];
        known[hint.level - 1] = hint.text;
        return { ...prev, [taskId]: known };
      });
    } catch {
      setHintError(true);
    }
  }

  return (
    <section aria-labelledby="lab-tasks-title">
      <h2 id="lab-tasks-title">{t('tasks')}</h2>
      <ol>
        {lab.tasks.map((task) => {
          const taskResults = results.filter((r) => r.taskId === task.taskId);
          const done = taskResults.length > 0 && taskResults.every((r) => r.outcome === 'passed');
          const shown = hints[task.taskId] ?? [];
          return (
            <li key={task.taskId}>
              <p>
                {task.text} <em>{task.required ? t('taskRequired') : t('taskOptional')}</em>
                {taskResults.length > 0 ? (
                  <strong> {done ? t('taskDone') : t('taskTodo')}</strong>
                ) : null}
              </p>
              {task.hintCount > 0 ? (
                <div>
                  {shown.length > 0 ? (
                    <ul aria-label={t('hints.title')}>
                      {shown.map((text, index) => (
                        <li key={index}>
                          <strong>{t('hints.level', { level: index + 1 })}</strong> {text}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                  {shown.length < task.hintCount && sessionId !== null ? (
                    <button
                      type="button"
                      onClick={() => void reveal(task.taskId, shown.length + 1)}
                    >
                      {t('hints.reveal', { level: shown.length + 1 })}
                    </button>
                  ) : null}
                </div>
              ) : null}
            </li>
          );
        })}
      </ol>
      {!hasHints ? <p>{t('hints.none')}</p> : null}
      {hintError ? <p role="alert">{t('hints.revealError')}</p> : null}
    </section>
  );
}

// --- active session ---------------------------------------------------------------------

interface ActiveLabProps {
  readonly lab: LabDetailView;
  readonly locale: string;
  readonly session: LabSessionView;
  readonly onPollNow: () => void;
  readonly onReplaced: (session: LabSessionView) => void;
  readonly describe: (cause: unknown) => string;
}

function resultsOf(state: LabViewState): readonly CheckResultView[] {
  if (state.phase === 'grading' || state.phase === 'results') return state.results;
  if (state.phase === 'reconnecting') return resultsOf(state.resume);
  return [];
}

function ActiveLab({ lab, locale, session, onPollNow, onReplaced, describe }: ActiveLabProps) {
  const t = useTranslations('Labs');
  const [view, dispatch] = useReducer(labViewReducer, initialLabViewState);
  const [now, setNow] = useState(() => Date.now());
  const [lastAcceptedAtMs, setLastAcceptedAtMs] = useState<number | null>(null);
  const [confirmingReset, setConfirmingReset] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [reconnected, setReconnected] = useState(false);
  const checkWorkRef = useRef<HTMLButtonElement | null>(null);
  const resetButtonRef = useRef<HTMLButtonElement | null>(null);
  const cancelResetRef = useRef<HTMLButtonElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const filesRef = useRef<Record<string, string>>(
    Object.fromEntries(lab.learnerFiles.map((name) => [name, ''])),
  );
  const saveTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const wasReconnecting = useRef(false);
  /** True from the moment this page requests a run until its result stream ends. */
  const ownRun = useRef(false);

  const isCode = lab.kind === 'code';
  const provisioning = session.status === 'provisioning';

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  useEffect(
    () => () => {
      abortRef.current?.abort();
      clearTimeout(saveTimer.current);
    },
    [],
  );

  // "Reconnected" is announced once after a reconnecting phase ends.
  useEffect(() => {
    if (view.phase === 'reconnecting') {
      wasReconnecting.current = true;
      setReconnected(false);
    } else if (wasReconnecting.current) {
      wasReconnecting.current = false;
      setReconnected(view.phase !== 'error');
    }
  }, [view.phase]);

  // A run that finished while we were not following it (page reload mid-run).
  useEffect(() => {
    if (session.status === 'ready' && view.phase === 'grading' && !ownRun.current) {
      dispatch({ type: 'grading-finished', runStatus: 'could-not-complete' });
    }
  }, [session.status, view.phase]);

  useEffect(() => {
    if (session.status === 'grading' && view.phase === 'ready') {
      dispatch({ type: 'grading-requested' });
    }
  }, [session.status, view.phase]);

  useEffect(() => {
    if (confirmingReset) cancelResetRef.current?.focus();
  }, [confirmingReset]);

  const serverNow = serverTimeMs(now, session.clockOffsetMs);
  const ttlRemaining = Math.max(0, session.ttlAtMs - serverNow);
  const idleRemaining = Math.max(0, session.idleDeadlineMs - serverNow);
  const slow = provisioning && serverNow - session.createdAtMs > config.provisionSlowMs;

  const running = view.phase === 'grading' || session.status === 'grading';
  const lastAccepted = useMemo(() => {
    const fromSession =
      session.gradingAvailableAtMs === null
        ? null
        : session.gradingAvailableAtMs - config.gradingMinIntervalMs;
    if (fromSession === null) return lastAcceptedAtMs;
    return lastAcceptedAtMs === null ? fromSession : Math.max(lastAcceptedAtMs, fromSession);
  }, [session.gradingAvailableAtMs, lastAcceptedAtMs]);
  const checkable =
    session.status === 'ready' &&
    canCheckWork(
      { runStatus: running ? 'running' : null, lastAcceptedAtMs: lastAccepted, nowMs: serverNow },
      config,
    );

  let disabledReason: string | null = null;
  if (!checkable) {
    if (provisioning) disabledReason = t('checkDisabledNotReady');
    else if (running) disabledReason = t('checkDisabledGrading');
    else {
      const wait = Math.max(
        1,
        Math.ceil(((lastAccepted ?? serverNow) + config.gradingMinIntervalMs - serverNow) / 1000),
      );
      disabledReason = t('checkDisabledThrottle', { seconds: wait });
    }
  }

  // --- grading --------------------------------------------------------------------------

  const follow = useCallback(
    async (runId: string) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      const delays = reconnectDelays(config);
      let lastEventId = 0;
      let attempt = 0;
      let lost = false;
      const sleep = (ms: number) =>
        new Promise<void>((resolve) => {
          const timer = setTimeout(resolve, ms);
          controller.signal.addEventListener('abort', () => {
            clearTimeout(timer);
            resolve();
          });
        });

      while (!controller.signal.aborted) {
        let finished = false;
        try {
          await streamRunEvents(
            session.stableId,
            runId,
            locale,
            lastEventId,
            (event) => {
              if (event.id > lastEventId && lost) {
                lost = false;
                attempt = 0;
                dispatch({ type: 'connection-restored' });
              }
              if (event.id > lastEventId) lastEventId = event.id;
              if (event.type === 'result') {
                dispatch({ type: 'check-result', result: event.result });
              } else {
                finished = true;
                dispatch({
                  type: 'grading-finished',
                  runStatus: event.status === 'running' ? 'could-not-complete' : event.status,
                });
              }
            },
            controller.signal,
          );
          if (finished) break;
          throw new Error('stream ended early');
        } catch (cause) {
          if (controller.signal.aborted) return;
          if (finished) break;
          if (cause instanceof LabApiError && cause.status >= 400 && cause.status < 500) {
            dispatch({ type: 'grading-failed', detail: describe(cause) });
            break;
          }
          if (!lost) {
            lost = true;
            dispatch({ type: 'connection-lost' });
          }
          if (attempt >= delays.length) {
            dispatch({ type: 'reconnect-gave-up' });
            break;
          }
          await sleep(delays[attempt] as number);
          attempt += 1;
          if (!controller.signal.aborted) dispatch({ type: 'reconnect-attempted' });
        }
      }
      if (abortRef.current === controller) {
        abortRef.current = null;
        ownRun.current = false;
      }
      onPollNow();
    },
    [session.stableId, locale, describe, onPollNow],
  );

  async function onCheck() {
    if (!checkable) return;
    ownRun.current = true;
    dispatch({ type: 'grading-requested' });
    try {
      if (isCode) {
        clearTimeout(saveTimer.current);
        await saveLabFiles(session.stableId, filesRef.current, locale);
      }
      const run = await requestGrading(session.stableId, locale, newKey());
      setLastAcceptedAtMs(run.acceptedAtMs);
      void follow(run.stableId);
    } catch (cause) {
      ownRun.current = false;
      dispatch({ type: 'grading-failed', detail: describe(cause) });
    }
  }

  // --- editor files -----------------------------------------------------------------------

  const editorFiles = useMemo(
    () => lab.learnerFiles.map((name) => ({ name, content: '' })),
    [lab.learnerFiles],
  );
  function onEdit(name: string, content: string) {
    filesRef.current = { ...filesRef.current, [name]: content };
    clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      saveLabFiles(session.stableId, filesRef.current, locale).then(
        () => setNotice(null),
        () => setNotice(t('errors.saveFailed')),
      );
    }, FILE_SAVE_DEBOUNCE_MS);
  }

  // --- reset ------------------------------------------------------------------------------

  async function onConfirmReset() {
    setResetting(true);
    try {
      abortRef.current?.abort();
      onReplaced(await resetLabSession(session.stableId, locale, newKey()));
    } catch (cause) {
      setNotice(describe(cause));
      setResetting(false);
      setConfirmingReset(false);
      resetButtonRef.current?.focus();
    }
  }

  function cancelReset() {
    setConfirmingReset(false);
    resetButtonRef.current?.focus();
  }

  // --- render -----------------------------------------------------------------------------

  const results = resultsOf(view);
  const ttl = formatClock(ttlRemaining);
  const idle = formatClock(idleRemaining);
  const warnings: string[] = [];
  if (!provisioning && warningActive(ttlRemaining, config)) {
    warnings.push(
      t('timer.ttlWarning', { minutes: Math.round(config.warningThresholdMs / 60000) }),
    );
  }
  if (!provisioning && warningActive(idleRemaining, config)) warnings.push(t('timer.idleWarning'));

  let gradingStatus = '';
  if (view.phase === 'grading') gradingStatus = t('checkRunning');
  else if (view.phase === 'results') {
    gradingStatus =
      view.runStatus === 'passed'
        ? t('results.allPassed')
        : view.runStatus === 'failed'
          ? t('results.someFailed')
          : t('results.gradingFailed');
  }
  let reconnectStatus = '';
  if (view.phase === 'reconnecting') reconnectStatus = t('reconnect.indicator');
  else if (view.phase === 'error' && view.error.kind === 'connection-lost') {
    reconnectStatus = t('reconnect.failed');
  } else if (reconnected) reconnectStatus = t('reconnect.restored');

  const reasonId = 'lab-check-reason';

  return (
    <>
      {provisioning ? (
        <section aria-labelledby="lab-provisioning-title">
          <h2 id="lab-provisioning-title">{t('status.provisioning')}</h2>
          <div role="status" aria-live="polite">
            {slow ? t('provisioning.slow') : ''}
          </div>
        </section>
      ) : null}

      <section aria-labelledby="lab-timer-title">
        <h2 id="lab-timer-title">{t('timer.label')}</h2>
        <p>
          <time>{t('timer.remaining', { minutes: ttl.minutes, seconds: ttl.seconds })}</time>
        </p>
        <p>
          {t('timer.idleLabel')}{' '}
          <time>{t('timer.remaining', { minutes: idle.minutes, seconds: idle.seconds })}</time>
        </p>
        <div role="status" aria-live="polite" data-testid="timer-warning">
          {warnings.map((w) => (
            <p key={w}>{w}</p>
          ))}
        </div>
      </section>

      <TaskList lab={lab} locale={locale} sessionId={session.stableId} results={results} />

      {!provisioning ? (
        isCode ? (
          <CodeEditor
            files={editorFiles}
            onChange={onEdit}
            onExit={() => checkWorkRef.current?.focus()}
          />
        ) : (
          <TerminalPane
            url={terminalUrl(labApiBaseUrl(), session.stableId)}
            config={config}
            checkWorkRef={checkWorkRef}
            onSessionStatus={onPollNow}
          />
        )
      ) : null}

      <section aria-labelledby="lab-actions-title">
        <h2 id="lab-actions-title">{t('results.title')}</h2>
        <button
          ref={checkWorkRef}
          type="button"
          aria-disabled={!checkable}
          aria-describedby={disabledReason === null ? undefined : reasonId}
          onClick={() => void onCheck()}
        >
          {t('checkWork')}
        </button>
        <p id={reasonId}>{disabledReason ?? ''}</p>

        <div role="status" aria-live="polite" data-testid="lab-results">
          {gradingStatus !== '' ? <p>{gradingStatus}</p> : null}
          {results.length > 0 ? (
            <ol aria-label={t('results.title')}>
              {results.map((r) => (
                <li key={`${r.taskId}:${r.checkId}`}>
                  <strong>
                    {r.outcome === 'passed'
                      ? t('results.passed')
                      : r.outcome === 'failed'
                        ? t('results.failed')
                        : t('results.gradingFailed')}
                  </strong>{' '}
                  {r.message}
                </li>
              ))}
            </ol>
          ) : null}
        </div>

        <div role="status" aria-live="polite" data-testid="lab-reconnect">
          {reconnectStatus}
        </div>
        {view.phase === 'error' ? (
          <div role="alert">
            <p>
              {view.error.kind === 'connection-lost' ? t('reconnect.failed') : view.error.detail}
            </p>
            {view.error.kind === 'connection-lost' ? (
              <button type="button" onClick={() => window.location.reload()}>
                {t('reconnect.reload')}
              </button>
            ) : (
              <button type="button" onClick={() => dispatch({ type: 'dismiss-results' })}>
                {t('resetCancel')}
              </button>
            )}
          </div>
        ) : null}
        {notice !== null ? <p role="alert">{notice}</p> : null}
      </section>

      <section aria-label={t('resetLab')}>
        <button
          ref={resetButtonRef}
          type="button"
          onClick={() => setConfirmingReset(true)}
          disabled={resetting}
        >
          {t('resetLab')}
        </button>
        {confirmingReset ? (
          <div
            role="group"
            aria-labelledby="lab-reset-title"
            aria-describedby="lab-reset-body"
            onKeyDown={(event) => {
              if (event.key === 'Escape') cancelReset();
            }}
          >
            <h3 id="lab-reset-title">{t('resetConfirmTitle')}</h3>
            <p id="lab-reset-body">{t('resetConfirmBody')}</p>
            <button type="button" onClick={() => void onConfirmReset()} disabled={resetting}>
              {t('resetConfirm')}
            </button>
            <button ref={cancelResetRef} type="button" onClick={cancelReset}>
              {t('resetCancel')}
            </button>
          </div>
        ) : null}
      </section>
    </>
  );
}
