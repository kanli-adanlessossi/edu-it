// Pure logic, schemas, and ports for the dashboard, track, and module pages
// (Req 19.1, 19.2, 19.6).
//
// Pages depend on the small `LearningApi` port declared here so behavior can be
// tested without a network; `learning-api.ts` satisfies it with the generated
// client. API payloads and form input are validated with Zod. Lock state is
// authoritative on the server (Req 2); the web only presents it.
import { z } from 'zod';

// --- API payload schemas (mirror apps/api learning_schemas.py) ----------------

const stableId = z.string().min(1).max(128);

export const lessonCompletionSchema = z.object({
  lesson_id: stableId,
  completed_at: z.string(),
});

export const moduleProgressSchema = z.object({
  module_id: stableId,
  ordinal: z.number().int(),
  state: z.string(),
  tested_out: z.boolean(),
  completions: z.array(lessonCompletionSchema),
});

export const masterySchema = z.object({
  concept_id: stableId,
  score: z.number().int().min(0).max(100),
  state: z.string(),
});

export const progressSchema = z.object({
  enrollment_id: stableId,
  track_id: stableId,
  modules: z.array(moduleProgressSchema),
  mastery: z.array(masterySchema),
});

export const enrollmentSchema = z.object({
  stable_id: stableId,
  track_id: stableId,
  enrolled_at: z.string(),
});

export const enrollmentPageSchema = z.object({
  items: z.array(enrollmentSchema),
  next_cursor: z.string().nullable(),
});

export const reviewScheduleEntrySchema = z.object({
  concept_id: stableId,
  due_at: z.string(),
});

export const reviewScheduleSchema = z.object({
  items: z.array(reviewScheduleEntrySchema),
});

// --- view models --------------------------------------------------------------

export type Progress = z.infer<typeof progressSchema>;
export type ModuleProgress = z.infer<typeof moduleProgressSchema>;
export type Mastery = z.infer<typeof masterySchema>;
export type Enrollment = z.infer<typeof enrollmentSchema>;
export type ReviewScheduleEntry = z.infer<typeof reviewScheduleEntrySchema>;

/** The slice of the API the learner pages need. */
export interface LearningApi {
  listEnrollments(): Promise<readonly Enrollment[]>;
  getProgress(trackId: string): Promise<Progress>;
  /** Review schedule entries (concept + due instant) for the learner. */
  getReviewSchedule(): Promise<readonly ReviewScheduleEntry[]>;
  enroll(trackId: string): Promise<Enrollment>;
}

// --- lock state ---------------------------------------------------------------

export type LockState = 'locked' | 'unlocked';

/**
 * Derive a module's presented lock state from the API state string. Anything
 * other than an explicit "unlocked" is shown locked (fail closed, Req 2.1);
 * a tested-out module is open (Req 21.3).
 */
export function deriveLockState(module: Pick<ModuleProgress, 'state' | 'tested_out'>): LockState {
  return module.tested_out || module.state === 'unlocked' ? 'unlocked' : 'locked';
}

/** Modules in authored order. */
export function sortModules(modules: readonly ModuleProgress[]): ModuleProgress[] {
  return [...modules].sort((a, b) => a.ordinal - b.ordinal);
}

/** Look up one module of a progress view. */
export function findModule(progress: Progress, moduleId: string): ModuleProgress | undefined {
  return progress.modules.find((m) => m.module_id === moduleId);
}

/** Share of modules not locked, as a whole percent 0–100 (0 when empty). */
export function unlockedPercent(modules: readonly ModuleProgress[]): number {
  if (modules.length === 0) return 0;
  const open = modules.filter((m) => deriveLockState(m) === 'unlocked').length;
  return Math.round((open / modules.length) * 100);
}

/** Total completed lessons across modules. */
export function completedLessonCount(modules: readonly ModuleProgress[]): number {
  return modules.reduce((n, m) => n + m.completions.length, 0);
}

// --- due / overdue ------------------------------------------------------------

export interface ReviewDueSplit {
  /** Due now: the due instant has been reached (inclusive) but not passed a full day. */
  readonly due: readonly ReviewScheduleEntry[];
  /** Overdue: due at least one whole day ago (decay applies per day, Req 8). */
  readonly overdue: readonly ReviewScheduleEntry[];
}

const DAY_MS = 24 * 60 * 60 * 1000;

/**
 * Split schedule entries into due and overdue purely from `now` (Req 9.6).
 * Entries not yet due are dropped. Entries with an unparseable instant are
 * ignored rather than guessed at.
 */
export function splitDueOverdue(
  entries: readonly ReviewScheduleEntry[],
  now: Date,
): ReviewDueSplit {
  const due: ReviewScheduleEntry[] = [];
  const overdue: ReviewScheduleEntry[] = [];
  for (const entry of entries) {
    const dueAt = Date.parse(entry.due_at);
    if (Number.isNaN(dueAt)) continue;
    const lateBy = now.getTime() - dueAt;
    if (lateBy < 0) continue;
    (lateBy >= DAY_MS ? overdue : due).push(entry);
  }
  return { due, overdue };
}

// --- Zod-validated form / route input ------------------------------------------

/** A stable id as it may appear in a route segment or a form field. */
export const stableIdParamSchema = z
  .string()
  .trim()
  .min(1)
  .max(128)
  .regex(/^[A-Za-z0-9._:-]+$/);

export const enrollFormSchema = z.object({ trackId: stableIdParamSchema });

export type EnrollFormResult =
  | { readonly ok: true; readonly trackId: string }
  | { readonly ok: false; readonly error: 'invalid' };

/** Validate the enroll form's raw fields before anything is sent. */
export function validateEnrollForm(fields: Readonly<Record<string, unknown>>): EnrollFormResult {
  const parsed = enrollFormSchema.safeParse(fields);
  return parsed.success
    ? { ok: true, trackId: parsed.data.trackId }
    : { ok: false, error: 'invalid' };
}

/** Validate a route segment as a stable id; undefined when malformed. */
export function parseRouteId(value: string): string | undefined {
  let decoded: string;
  try {
    decoded = decodeURIComponent(value);
  } catch {
    return undefined;
  }
  const parsed = stableIdParamSchema.safeParse(decoded);
  return parsed.success ? parsed.data : undefined;
}
