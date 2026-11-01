// @vitest-environment jsdom
//
// Component tests for the Lab_View states (hands-on-labs task 16.3).
//
// Validates: Requirements 15.3, 15.4, 15.5, 15.6, 15.7, 15.9, 15.12, 25.4.
//
// The lab-api network functions are mocked (the pure helpers and `LabApiError`
// stay real), the terminal and editor are replaced by light stand-ins, and
// timers are faked so the 10 s rule, the 20 s slow-provisioning message, the
// polling, and the reconnect back-off run on a controlled clock.
//
// Keyboard operation: every action is a native `<button type="button">`, which
// a browser activates with Enter or Space by firing `click`. jsdom does not
// synthesise that click, so the tests call `click()` (the event the keyboard
// produces) and assert the elements are real, focusable buttons. Escape in the
// reset confirmation is a real key handler and is dispatched as a key event.
import { NextIntlClientProvider } from 'next-intl';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import en from '../messages/en.json';
import * as api from '../lab-api.js';
import { reconnectDelays, type CheckResultView } from '../lab-state.js';
import { LabView } from './lab-view.js';

vi.mock('../lab-api.js', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lab-api.js')>();
  return {
    ...actual,
    findActiveSession: vi.fn(),
    fetchLabSession: vi.fn(),
    startLabSession: vi.fn(),
    resetLabSession: vi.fn(),
    findAnyActiveSession: vi.fn(),
    endLabSession: vi.fn(),
    requestGrading: vi.fn(),
    revealHint: vi.fn(),
    saveLabFiles: vi.fn(),
    streamRunEvents: vi.fn(),
  };
});

vi.mock('./terminal-pane.js', () => ({
  TerminalPane: () => <div data-testid="terminal" />,
}));

vi.mock('./code-editor.js', () => ({
  CodeEditor: (props: {
    onChange: (name: string, content: string) => void;
    onExit: () => void;
  }) => (
    <div data-testid="editor">
      <textarea
        aria-label="editor"
        onChange={(event) => props.onChange('main.py', event.target.value)}
      />
      <button type="button" onClick={props.onExit}>
        {LEAVE_EDITOR}
      </button>
    </div>
  ),
}));

const LEAVE_EDITOR = 'leave-editor';
const mocked = vi.mocked(api);
const NOW = Date.parse('2030-01-01T12:00:00Z');
const MINUTE = 60_000;

function session(overrides: Partial<api.LabSessionView> = {}): api.LabSessionView {
  return {
    stableId: 'sess-1',
    labId: 'lab-1',
    status: 'ready',
    createdAtMs: NOW - 60_000,
    ttlAtMs: NOW + 30 * MINUTE,
    idleDeadlineMs: NOW + 20 * MINUTE,
    gradingAvailableAtMs: null,
    clockOffsetMs: 0,
    error: null,
    ...overrides,
  };
}

function lab(overrides: Partial<api.LabDetailView> = {}): api.LabDetailView {
  return {
    labId: 'lab-1',
    kind: 'terminal',
    title: 'Navigate the shell',
    locale: 'en',
    tasks: [
      { taskId: 't1', required: true, text: 'List the files', hintCount: 3 },
      { taskId: 't2', required: false, text: 'Print the path', hintCount: 0 },
    ],
    learnerFiles: ['main.py'],
    ...overrides,
  };
}

function result(
  taskId: string,
  checkId: string,
  outcome: CheckResultView['outcome'],
  message = `${checkId} ${outcome}`,
): CheckResultView {
  return { taskId, checkId, outcome, message };
}

/** A controllable result stream: the test decides what arrives and when it ends. */
interface FakeStream {
  readonly lastEventId: number;
  emit(event: api.RunStreamEvent): void;
  end(): void;
  fail(cause: unknown): void;
}
let streams: FakeStream[];

let container: HTMLElement;
let root: Root;
let current: api.LabSessionView | null;

async function flush() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

async function mount(props: { lab?: api.LabDetailView; session?: api.LabSessionView | null } = {}) {
  current = props.session === undefined ? session() : props.session;
  mocked.findActiveSession.mockImplementation(async () => current);
  mocked.fetchLabSession.mockImplementation(async () => current as api.LabSessionView);
  await act(async () => {
    root.render(
      <NextIntlClientProvider locale="en" messages={en}>
        <LabView locale="en" lab={props.lab ?? lab()} />
      </NextIntlClientProvider>,
    );
  });
  await flush();
}

function button(name: string | RegExp): HTMLButtonElement {
  const found = Array.from(container.querySelectorAll('button')).find((b) =>
    typeof name === 'string' ? b.textContent === name : name.test(b.textContent ?? ''),
  );
  if (!found) throw new Error(`no button named ${String(name)}`);
  return found;
}

function queryButton(name: string | RegExp): HTMLButtonElement | null {
  try {
    return button(name);
  } catch {
    return null;
  }
}

async function click(element: HTMLElement) {
  await act(async () => {
    element.click();
  });
  await flush();
}

const text = () => container.textContent ?? '';
const region = (id: string) => container.querySelector(`[data-testid="${id}"]`)?.textContent ?? '';

/** Start a grading run and return the live stream for it. */
async function startCheck(): Promise<FakeStream> {
  mocked.requestGrading.mockResolvedValue({
    stableId: 'run-1',
    sessionId: 'sess-1',
    status: 'running',
    acceptedAtMs: Date.now(),
    results: [],
  });
  await click(button('Check my work'));
  const stream = streams.at(-1);
  if (!stream) throw new Error('no stream opened');
  return stream;
}

beforeEach(() => {
  vi.useFakeTimers({ now: NOW });
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  streams = [];
  mocked.streamRunEvents.mockImplementation(
    (_session, _run, _locale, lastEventId, onEvent) =>
      new Promise<void>((resolve, reject) => {
        streams.push({
          lastEventId,
          emit: (event) => onEvent(event),
          end: () => resolve(),
          fail: (cause) => reject(cause),
        });
      }),
  );
  mocked.saveLabFiles.mockResolvedValue(undefined);
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe('Lab_View: ready state', () => {
  it('shows the tasks, the terminal, and an enabled "Check my work" (Req 15.3)', async () => {
    await mount();
    expect(text()).toContain('Navigate the shell');
    expect(text()).toContain('List the files');
    expect(container.querySelector('[data-testid="terminal"]')).not.toBeNull();
    const check = button('Check my work');
    expect(check.getAttribute('aria-disabled')).toBe('false');
    expect(check.getAttribute('aria-describedby')).toBeNull();
  });

  it('shows the editor for a code lab', async () => {
    await mount({ lab: lab({ kind: 'code' }) });
    expect(container.querySelector('[data-testid="editor"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="terminal"]')).toBeNull();
  });

  it('offers to start a lab when there is no active session', async () => {
    await mount({ session: null });
    mocked.startLabSession.mockImplementation(async () => {
      current = session();
      return current;
    });
    await click(button('Start lab'));
    expect(mocked.startLabSession).toHaveBeenCalledTimes(1);
    expect(button('Check my work')).toBeTruthy();
  });

  it("offers to end another lab's running session after a 409 and then starts this lab (Req 30.1)", async () => {
    await mount({ session: null });
    const other = session({ stableId: 'sess-other', labId: 'lab-other' });
    mocked.startLabSession.mockRejectedValueOnce(
      new api.LabApiError('Lab lab-other is running. End it first.', 409),
    );
    await click(button('Start lab'));
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('lab-other');
    expect(mocked.endLabSession).not.toHaveBeenCalled();

    mocked.findAnyActiveSession.mockResolvedValue(other);
    mocked.endLabSession.mockResolvedValue(session({ ...other, status: 'ended' }));
    mocked.startLabSession.mockImplementation(async () => {
      current = session();
      return current;
    });
    await click(button('End the running lab and start this one'));
    expect(mocked.endLabSession).toHaveBeenCalledWith('sess-other', 'en');
    expect(mocked.startLabSession).toHaveBeenCalledTimes(2);
    expect(button('Check my work')).toBeTruthy();
  });
});

describe('Lab_View: check-my-work enable/disable rules (Req 15.4, 15.5)', () => {
  it('stays focusable (aria-disabled, not disabled) and explains why while provisioning', async () => {
    await mount({ session: session({ status: 'provisioning', createdAtMs: NOW }) });
    const check = button('Check my work');
    expect(check.disabled).toBe(false);
    expect(check.getAttribute('aria-disabled')).toBe('true');
    const reason = container.querySelector(`#${check.getAttribute('aria-describedby')}`);
    expect(reason?.textContent).toBe('The lab is not ready yet.');
    check.focus();
    expect(document.activeElement).toBe(check);
    await click(check);
    expect(mocked.requestGrading).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="terminal"]')).toBeNull();
  });

  it('is unavailable with a reason while a check is already running', async () => {
    await mount({ session: session({ status: 'grading' }) });
    const check = button('Check my work');
    expect(check.getAttribute('aria-disabled')).toBe('true');
    expect(container.querySelector('#lab-check-reason')?.textContent).toBe(
      'A check is already running.',
    );
    await click(check);
    expect(mocked.requestGrading).not.toHaveBeenCalled();
  });

  it('applies the 10 s rule after an accepted check and re-enables when it elapses', async () => {
    await mount();
    const stream = await startCheck();
    stream.emit({ type: 'run', id: 1, status: 'passed' });
    stream.end();
    await flush();

    const check = button('Check my work');
    expect(check.getAttribute('aria-disabled')).toBe('true');
    expect(container.querySelector('#lab-check-reason')?.textContent).toMatch(
      /Please wait \d+ s before checking again\./,
    );
    await click(check);
    expect(mocked.requestGrading).toHaveBeenCalledTimes(1);

    await advance(11_000);
    expect(button('Check my work').getAttribute('aria-disabled')).toBe('false');
  });

  it('honours the server-provided next-available instant after a reload', async () => {
    await mount({ session: session({ gradingAvailableAtMs: Date.now() + 5_000 }) });
    expect(button('Check my work').getAttribute('aria-disabled')).toBe('true');
    await advance(6_000);
    expect(button('Check my work').getAttribute('aria-disabled')).toBe('false');
  });
});

describe('Lab_View: grading and results (Req 15.4, 15.6, 25.4)', () => {
  it('announces grading and shows results incrementally in a polite live region', async () => {
    await mount();
    const live = container.querySelector('[data-testid="lab-results"]');
    expect(live?.getAttribute('role')).toBe('status');
    expect(live?.getAttribute('aria-live')).toBe('polite');

    const stream = await startCheck();
    expect(region('lab-results')).toContain('Checking your work…');
    expect(mocked.requestGrading).toHaveBeenCalledTimes(1);

    await act(async () =>
      stream.emit({ type: 'result', id: 1, result: result('t1', 'c1', 'passed') }),
    );
    expect(region('lab-results')).toContain('c1 passed');
    expect(region('lab-results')).toContain('Checking your work…');

    await act(async () =>
      stream.emit({ type: 'result', id: 2, result: result('t1', 'c2', 'failed') }),
    );
    expect(region('lab-results')).toContain('c2 failed');
  });

  it('shows the success state when the run passes', async () => {
    await mount();
    const stream = await startCheck();
    await act(async () => {
      stream.emit({ type: 'result', id: 1, result: result('t1', 'c1', 'passed') });
      stream.emit({ type: 'run', id: 2, status: 'passed' });
      stream.end();
    });
    await flush();
    expect(region('lab-results')).toContain('All required tasks are complete.');
    expect(text()).toContain('Completed');
  });

  it('shows feedback and keeps the lab usable when checks fail', async () => {
    await mount();
    const stream = await startCheck();
    await act(async () => {
      stream.emit({ type: 'result', id: 1, result: result('t1', 'c1', 'failed') });
      stream.emit({ type: 'run', id: 2, status: 'failed' });
      stream.end();
    });
    await flush();
    expect(region('lab-results')).toContain('Some checks did not pass');
    expect(text()).toContain('Not completed yet');
  });

  it('reports a run that could not complete', async () => {
    await mount();
    const stream = await startCheck();
    await act(async () => {
      stream.emit({ type: 'run', id: 1, status: 'could-not-complete' });
      stream.end();
    });
    await flush();
    expect(region('lab-results')).toContain('We could not complete the check.');
  });

  it('saves the editor files before grading a code lab', async () => {
    await mount({ lab: lab({ kind: 'code' }) });
    const editor = container.querySelector('textarea') as HTMLTextAreaElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
      setter?.call(editor, 'print(1)');
      editor.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await startCheck();
    expect(mocked.saveLabFiles).toHaveBeenCalledWith('sess-1', { 'main.py': 'print(1)' }, 'en');
    expect(mocked.requestGrading).toHaveBeenCalledTimes(1);
  });
});

describe('Lab_View: error states (Req 15.7)', () => {
  it('shows the translated detail when the grading request fails and lets the learner dismiss it', async () => {
    await mount();
    mocked.requestGrading.mockRejectedValue(new api.LabApiError('Please wait a moment.', 429));
    await click(button('Check my work'));
    const alert = container.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain('Please wait a moment.');
    await click(button('Cancel'));
    expect(container.querySelector('[role="alert"]')).toBeNull();
  });

  it('falls back to the generic message for an unexpected failure', async () => {
    await mount();
    mocked.requestGrading.mockRejectedValue(new Error('boom'));
    await click(button('Check my work'));
    expect(container.querySelector('[role="alert"]')?.textContent).toContain(
      'Something went wrong. Please try again.',
    );
  });

  it('shows a 4xx stream error as a grading error', async () => {
    await mount();
    const stream = await startCheck();
    await act(async () => stream.fail(new api.LabApiError('Run not found.', 404)));
    await flush();
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('Run not found.');
  });

  it('shows a failed start with a retry action', async () => {
    await mount({
      session: session({ status: 'failed', error: { title: 'x', detail: 'Image pull failed.' } }),
    });
    expect(text()).toContain('Your lab could not start');
    expect(container.querySelector('[role="alert"]')?.textContent).toBe('Image pull failed.');
    mocked.startLabSession.mockImplementation(async () => {
      current = session();
      return current;
    });
    await click(button('Try again'));
    expect(mocked.startLabSession).toHaveBeenCalledTimes(1);
  });

  it.each([
    ['expired', 'Your lab time ran out.'],
    ['idle-expired', 'Your lab ended because of inactivity.'],
    ['ended', 'The session ended while you were away.'],
  ] as const)('explains an %s session and offers a new one (Req 15.12)', async (status, body) => {
    await mount({ session: session({ status }) });
    expect(text()).toContain('Your lab session has ended');
    expect(container.querySelector('[role="status"]')?.textContent).toContain(body);
    expect(button('Start a new session')).toBeTruthy();
  });

  it('shows the ended message when the session disappears (404 on poll)', async () => {
    await mount();
    mocked.fetchLabSession.mockRejectedValue(new api.LabApiError('', 404));
    await advance(2_100);
    expect(text()).toContain('This lab session no longer exists.');
    expect(button('Start a new session')).toBeTruthy();
  });
});

describe('Lab_View: reconnecting (Req 15.9, 25.4)', () => {
  const status = () => region('lab-reconnect');

  it('indicates a lost connection, resumes after the back-off, and announces restore on a new event only', async () => {
    await mount();
    const first = await startCheck();
    await act(async () =>
      first.emit({ type: 'result', id: 1, result: result('t1', 'c1', 'passed') }),
    );
    expect(status()).toBe('');

    await act(async () => first.fail(new Error('network')));
    expect(status()).toBe('Connection lost. Reconnecting…');
    expect(container.querySelector('[data-testid="lab-reconnect"]')?.getAttribute('role')).toBe(
      'status',
    );
    // Results already received stay visible.
    expect(region('lab-results')).toContain('c1 passed');

    await advance(1_000);
    expect(streams).toHaveLength(2);
    expect(streams[1]?.lastEventId).toBe(1);
    expect(status()).toBe('Connection lost. Reconnecting…');

    // A replayed event is not new: still reconnecting.
    await act(async () =>
      streams[1]?.emit({ type: 'result', id: 1, result: result('t1', 'c1', 'passed') }),
    );
    expect(status()).toBe('Connection lost. Reconnecting…');

    await act(async () =>
      streams[1]?.emit({ type: 'result', id: 2, result: result('t1', 'c2', 'passed') }),
    );
    expect(status()).toBe('Connection restored.');
    expect(region('lab-results')).toContain('c2 passed');
  });

  it('gives up after the back-off schedule and offers a reload', async () => {
    await mount();
    const first = await startCheck();
    await act(async () => first.fail(new Error('network')));
    const delays = reconnectDelays(api.labClientConfig);
    // Every dropped stream is followed by one back-off wait and one more attempt.
    for (const delay of delays) {
      await advance(delay);
      await act(async () => streams.at(-1)?.fail(new Error('network')));
    }
    await flush();
    expect(streams).toHaveLength(delays.length + 1);
    expect(container.querySelector('[role="alert"]')?.textContent).toContain(
      'We could not reconnect. Reload the page to try again.',
    );
    expect(status()).toBe('We could not reconnect. Reload the page to try again.');
    expect(button('Reload').type).toBe('button');
  });
});

describe('Lab_View: slow provisioning (Req 15.3)', () => {
  const slowRegion = () =>
    container.querySelector('#lab-provisioning-title + [role="status"]')?.textContent;

  it('shows the preparing state without the slow message at first', async () => {
    await mount({ session: session({ status: 'provisioning', createdAtMs: NOW - 5_000 }) });
    expect(text()).toContain('Preparing your lab…');
    expect(slowRegion()).toBe('');
  });

  it('shows the slow message once provisioning passes 20 s', async () => {
    await mount({ session: session({ status: 'provisioning', createdAtMs: NOW - 5_000 }) });
    await advance(16_000);
    expect(slowRegion()).toContain('taking longer than usual');
    expect(
      container
        .querySelector('#lab-provisioning-title + [role="status"]')
        ?.getAttribute('aria-live'),
    ).toBe('polite');
  });

  it('shows the slow message immediately when provisioning is already past 20 s', async () => {
    await mount({ session: session({ status: 'provisioning', createdAtMs: NOW - 21_000 }) });
    expect(slowRegion()).toContain('taking longer than usual');
  });
});

describe('Lab_View: timer warning', () => {
  it('warns below the threshold in a polite live region', async () => {
    await mount({ session: session({ ttlAtMs: NOW + MINUTE }) });
    const warning = container.querySelector('[data-testid="timer-warning"]');
    expect(warning?.getAttribute('role')).toBe('status');
    expect(warning?.textContent).toContain('Your lab will end in less than 2 minutes.');
  });

  it('shows no warning with plenty of time left', async () => {
    await mount();
    expect(region('timer-warning')).toBe('');
  });
});

describe('Lab_View: hint progression (Req 15.4)', () => {
  it('reveals one level at a time and hides the button when all are shown', async () => {
    await mount();
    mocked.revealHint.mockImplementation(async (_s, taskId, level) => ({
      taskId,
      level,
      totalLevels: 3,
      text: `hint text ${level}`,
    }));

    expect(queryButton('Show hint 2')).toBeNull();
    await click(button('Show hint 1'));
    expect(mocked.revealHint).toHaveBeenLastCalledWith('sess-1', 't1', 1, 'en');
    expect(text()).toContain('hint text 1');
    expect(queryButton('Show hint 1')).toBeNull();

    await click(button('Show hint 2'));
    expect(mocked.revealHint).toHaveBeenLastCalledWith('sess-1', 't1', 2, 'en');
    expect(text()).toContain('hint text 1');
    expect(text()).toContain('hint text 2');

    await click(button('Show hint 3'));
    expect(text()).toContain('hint text 3');
    expect(queryButton(/Show hint/)).toBeNull();
  });

  it('reports a hint that could not be revealed and allows trying again', async () => {
    await mount();
    mocked.revealHint.mockRejectedValueOnce(new Error('nope'));
    await click(button('Show hint 1'));
    expect(container.querySelector('[role="alert"]')?.textContent).toContain(
      'We could not show this hint.',
    );
    expect(button('Show hint 1')).toBeTruthy();
  });

  it('offers no hint buttons when the lab has no hints', async () => {
    await mount({
      lab: lab({ tasks: [{ taskId: 't1', required: true, text: 'Only task', hintCount: 0 }] }),
    });
    expect(queryButton(/Show hint/)).toBeNull();
    expect(text()).toContain('No hints are available for this lab.');
  });
});

describe('Lab_View: keyboard operation of every action (Req 15.12, 25.4)', () => {
  it('exposes every action as a focusable native button', async () => {
    await mount();
    const buttons = Array.from(container.querySelectorAll('button'));
    expect(buttons.length).toBeGreaterThanOrEqual(3);
    for (const b of buttons) {
      expect(b.type).toBe('button');
      expect(b.tabIndex).toBeGreaterThanOrEqual(0);
      expect(b.disabled).toBe(false);
      b.focus();
      expect(document.activeElement).toBe(b);
    }
  });

  it('runs a check from the keyboard-activated button', async () => {
    await mount();
    const check = button('Check my work');
    check.focus();
    mocked.requestGrading.mockResolvedValue({
      stableId: 'run-1',
      sessionId: 'sess-1',
      status: 'running',
      acceptedAtMs: Date.now(),
      results: [],
    });
    await click(check);
    expect(mocked.requestGrading).toHaveBeenCalledTimes(1);
  });

  it('moves focus from the editor to "Check my work" with the exit action', async () => {
    await mount({ lab: lab({ kind: 'code' }) });
    await click(button('leave-editor'));
    expect(document.activeElement).toBe(button('Check my work'));
  });

  it('moves focus into the reset confirmation, closes it with Escape, and restores focus', async () => {
    await mount();
    const reset = button('Reset lab');
    await click(reset);
    const group = container.querySelector('[role="group"]') as HTMLElement;
    expect(group.textContent).toContain('Reset this lab?');
    expect(document.activeElement).toBe(button('Cancel'));

    await act(async () => {
      button('Cancel').dispatchEvent(
        new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }),
      );
    });
    expect(container.querySelector('[role="group"]')).toBeNull();
    expect(document.activeElement).toBe(reset);
    expect(mocked.resetLabSession).not.toHaveBeenCalled();
  });

  it('resets only after the confirmation button is activated', async () => {
    await mount();
    mocked.resetLabSession.mockImplementation(async () => {
      current = session({ stableId: 'sess-2' });
      return current;
    });
    await click(button('Reset lab'));
    expect(mocked.resetLabSession).not.toHaveBeenCalled();
    await click(button('Reset'));
    expect(mocked.resetLabSession).toHaveBeenCalledWith('sess-1', 'en', expect.any(String));
    expect(container.querySelector('[role="group"]')).toBeNull();
  });
});
