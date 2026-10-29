// Pure logic and ports for the quiz player, review session, and project
// submission pages (Req 19.4, 19.5, 19.6).
//
// Pages depend on the small ports declared here so behavior can be tested
// without a network; `practice-api.ts` satisfies them with the generated client.
// Forms are validated with Zod before anything is sent (design: "validating
// forms with Zod"). The API stays authoritative: client validation only spares
// a round trip for submissions the Learning_API would reject (Req 20.1, 20.2).

import { z } from 'zod';

// --- shared view models -------------------------------------------------------

/** A selectable option; correctness is never exposed to the client. */
export interface PracticeOption {
  readonly optionId: number;
  readonly text: string;
}

/** A question as shown in the quiz player. */
export interface QuizQuestionView {
  readonly stableId: string;
  readonly prompt: string;
  readonly options: readonly PracticeOption[];
  /** Progressive hint texts, ordered from level 1 upward. */
  readonly hints: readonly string[];
}

export interface QuizView {
  readonly stableId: string;
  readonly title: string;
  readonly contentVersion?: string;
  readonly questions: readonly QuizQuestionView[];
}

export interface MasteryView {
  readonly conceptId: string;
  readonly score: number;
  readonly state: string;
}

export interface QuizAttemptResult {
  readonly score: number;
  readonly bestScore: number;
  readonly replayed: boolean;
  /** Per question: whether the answer counted toward mastery (24h rule). */
  readonly countedForMastery: Readonly<Record<string, boolean>>;
  readonly mastery: readonly MasteryView[];
}

export interface HintResult {
  readonly questionId: string;
  readonly hintLevel: number;
}

export interface ReviewQuestionView {
  readonly stableId: string;
  readonly prompt: string;
  readonly options: readonly PracticeOption[];
}

export interface ReviewResult {
  readonly passed: boolean;
  readonly score: number;
  readonly dueAt: string;
  readonly intervalSeconds: number;
}

export interface RubricCriterionView {
  readonly stableId: string;
  readonly title: string;
  readonly maxPoints: number;
}

export interface ProjectView {
  readonly stableId: string;
  readonly title: string;
  readonly description: string;
  readonly rubric: readonly RubricCriterionView[];
}

export interface ProjectSubmissionResult {
  readonly stableId: string;
  readonly status: 'pending' | 'reviewed';
  readonly reviewOutcome: string | null;
}

/** Thrown by the API adapter with only the learner-facing translated detail. */
export class PracticeApiError extends Error {
  constructor(
    readonly detail: string,
    readonly status: number,
  ) {
    super(detail);
    this.name = 'PracticeApiError';
  }
}

// --- ports --------------------------------------------------------------------

export interface QuizApi {
  attempt(
    quizId: string,
    selections: Readonly<Record<string, number | null>>,
    options: { idempotencyKey: string; contentVersion?: string },
  ): Promise<QuizAttemptResult>;
  revealHint(quizId: string, questionId: string): Promise<HintResult>;
}

export interface ReviewApi {
  submit(
    conceptId: string,
    selections: Readonly<Record<string, number | null>>,
  ): Promise<ReviewResult>;
}

export interface ProjectApi {
  submit(input: ProjectSubmissionInput): Promise<ProjectSubmissionResult>;
}

// --- quiz / review selections --------------------------------------------------

/** Every question gets an entry; unanswered questions are explicit `null`s. */
export function buildSelections(
  questionIds: readonly string[],
  chosen: Readonly<Record<string, number>>,
): Record<string, number | null> {
  const selections: Record<string, number | null> = {};
  for (const id of questionIds) {
    selections[id] = chosen[id] ?? null;
  }
  return selections;
}

/** The hint texts a learner may currently see (levels 1..revealedLevel). */
export function visibleHints(hints: readonly string[], revealedLevel: number): readonly string[] {
  const level = Math.max(0, Math.min(revealedLevel, hints.length));
  return hints.slice(0, level);
}

/** Whether another progressive hint is still available for the question. */
export function canRevealHint(hints: readonly string[], revealedLevel: number): boolean {
  return revealedLevel < hints.length;
}

// --- project submission form ---------------------------------------------------

const evidenceItem = z.string().trim().min(1);

/** Wire shape of a submission; mirrors the API's `ProjectSubmissionRequest`. */
export const projectSubmissionSchema = z.object({
  projectId: z.string().min(1),
  selfAssessment: z.record(z.string(), z.number().int().min(0)),
  evidence: z.array(evidenceItem).min(1),
});

export type ProjectSubmissionInput = z.infer<typeof projectSubmissionSchema>;

export type ProjectFormErrorCode = 'criterionMissing' | 'criterionOutOfRange' | 'evidenceRequired';

export interface ProjectFormError {
  readonly code: ProjectFormErrorCode;
  /** The rubric criterion concerned, when the error is per-criterion. */
  readonly criterionId?: string;
}

export type ProjectFormResult =
  | { readonly ok: true; readonly input: ProjectSubmissionInput }
  | { readonly ok: false; readonly errors: readonly ProjectFormError[] };

/**
 * Validate the raw form state against the project's rubric: the
 * self-assessment must cover every criterion with points in [0, max_points],
 * and at least one non-blank evidence item is required (Req 20.1, 20.2).
 */
export function validateProjectForm(
  project: Pick<ProjectView, 'stableId' | 'rubric'>,
  rawPoints: Readonly<Record<string, string>>,
  rawEvidence: readonly string[],
): ProjectFormResult {
  const errors: ProjectFormError[] = [];
  const selfAssessment: Record<string, number> = {};

  for (const criterion of project.rubric) {
    const raw = (rawPoints[criterion.stableId] ?? '').trim();
    if (raw === '' || !/^\d+$/.test(raw)) {
      errors.push({ code: 'criterionMissing', criterionId: criterion.stableId });
      continue;
    }
    const points = Number(raw);
    if (points > criterion.maxPoints) {
      errors.push({ code: 'criterionOutOfRange', criterionId: criterion.stableId });
      continue;
    }
    selfAssessment[criterion.stableId] = points;
  }

  const evidence = rawEvidence.map((item) => item.trim()).filter((item) => item.length > 0);
  if (evidence.length === 0) {
    errors.push({ code: 'evidenceRequired' });
  }

  if (errors.length > 0) {
    return { ok: false, errors };
  }
  const parsed = projectSubmissionSchema.safeParse({
    projectId: project.stableId,
    selfAssessment,
    evidence,
  });
  if (!parsed.success) {
    return { ok: false, errors: [{ code: 'evidenceRequired' }] };
  }
  return { ok: true, input: parsed.data };
}
