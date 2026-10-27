---
inclusion: fileMatch
fileMatchPattern: "lab-images/**|apps/api/**/{lab,labs,sandbox,terminal}*/**|apps/api/**/*{lab,sandbox,terminal}*|apps/worker/**/{lab,labs,sandbox,terminal,grading,reaper}*/**|apps/worker/**/*{lab,sandbox,terminal,grading,reaper}*|apps/web/**/*{lab,terminal,xterm}*|**/*sandbox*|**/*{SandboxProvider,sandbox_provider}*"
---

# Labs, Sandboxes and Terminal

This steering file is the single source of truth for the EDU-IT labs, sandbox and terminal
rules. It is included for lab image definitions, sandbox code, and lab/terminal code
(conditional fileMatch inclusion). The labs, sandbox, and terminal rules below are transcribed
verbatim from the project rules; all names, numbers, and normative wording are preserved
unchanged. Where a rule is not self-explanatory, a one-line reason is added and marked `Reason:`.

## Mastery Mechanisms (labs)

- Learners rebuild skills step by step and advance only by proving mastery through quizzes,
  hands-on labs (real terminals, graded code) and projects.

## Sandbox Port

- Sandbox: Behind `SandboxProvider`: Docker (dev only), Kubernetes (prod), fake (tests).
- The sandbox sits behind the `SandboxProvider` port: Docker (dev only), Kubernetes (prod),
  fake (tests).
- `SandboxProvider`: implementations are Docker (dev only), Kubernetes (prod), Fake (tests);
  warm-pool extension point.

The `SandboxProvider` interface signatures (illustrative pseudocode; the authoritative versions
live in `docs/LABS-DESIGN.md` (D4)):

```pascal
INTERFACE SandboxProvider              // see docs/LABS-DESIGN.md (D4)
  FUNCTION provision(spec) RETURNS SandboxHandle
  FUNCTION exec(handle, command, as_user, env, cwd) RETURNS ExecResult
  FUNCTION attach_terminal(handle) RETURNS TerminalChannel
  FUNCTION copy_in(handle, source_ro, dest) RETURNS Void    // read-only source for grading
  FUNCTION copy_out(handle, source, dest) RETURNS Void
  FUNCTION destroy(handle) RETURNS Void
  FUNCTION health(handle) RETURNS HealthStatus
  // Implementations: Docker (dev only), Kubernetes (prod), Fake (tests). Warm-pool extension point.
END INTERFACE
```

## Terminal

- `apps/web`: renders the xterm.js terminal.
- `apps/api`: hosts the authenticated terminal WebSocket.
- `apps/worker`: lab provisioning and grading orchestration, reaper for expired sessions.
- The api can attach terminal TTY to the sandbox.

## Runtime and Topology (labs)

- labs namespace — restricted PSA; one pod per session.
- Dedicated lab node pool with taints/tolerations; configurable RuntimeClass (gVisor or Kata;
  plain runc dev-only with a startup warning).
- Default-deny NetworkPolicy in `labs`.
- Graceful shutdown drains terminal WebSockets with a reconnect message.
- The worker provisions, executes and destroys the per-session pool.

## Grading Bounds

- Grading is bounded: results within a 15 s target, executed at most once per 10 s per session.

## Failure Scenarios

### Sandbox provisioning failure

- IF sandbox provisioning fails, the system surfaces a translated error, leaves no partial
  session running, lets the reaper clean orphaned resources, and applies provisioning
  rate-limits and retries bounded by the maximum values stated in `docs/LABS-DESIGN.md`.

### WebSocket drop

- IF a terminal WebSocket connection drops, the system re-verifies session ownership on
  reconnect, presents a reconnect indication, drains active sockets on graceful shutdown, and
  persists no terminal I/O.

### Grading check timeout

- IF a grading check does not return within the 15 s target, grading is bounded (results within
  a 15 s target, executed at most once per 10 s per session), the timeout yields a translated
  "could not complete" feedback instead of raw output, and the run is recorded with a failed
  status.
