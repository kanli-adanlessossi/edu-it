// Pure logic and ports for the reviewer queue pages (ai-content Req 15.2, 15.3, 15.9).
//
// The pages depend on the small `ReviewerApi` port declared here so behavior can
// be tested without a network; `review-tasks-api.ts` satisfies it with the
// generated client. The API stays authoritative for expertise (403), double
// decisions (409) and validation (422); client checks only spare a round trip.

export type ReviewAction = 'approve' | 'edit' | 'requestChanges' | 'reject';

/** The decisions that close the task without creating a new one. */
export type DecisionAction = 'approve' | 'requestChanges' | 'reject';

export interface ReviewTaskView {
  readonly stableId: string;
  readonly kind: string;
  readonly targetKind: string;
  readonly targetStableId: string;
  readonly trackId: string;
  readonly state: string;
  readonly createdAt: string;
  readonly revisionContentVersion: string | null;
  readonly reason: string | null;
  readonly comment: string | null;
  readonly reviewerId: string | null;
  readonly decidedAt: string | null;
}

export interface ReviewTaskPage {
  readonly items: readonly ReviewTaskView[];
  readonly nextCursor: string | null;
}

export interface VariantView {
  readonly stableId: string;
  readonly parentQuestionId: string;
  readonly trackId: string;
  readonly state: string;
  readonly approvedContentVersion: string | null;
}

export interface FindingView {
  readonly kind: string;
  readonly target: string;
  readonly detail: string;
}

export interface RevisionView {
  readonly contentVersion: string;
  readonly state: string;
  readonly body: VariantBody;
  readonly origin: string | null;
  readonly model: string | null;
  readonly promptId: string | null;
  readonly promptVersion: string | null;
  readonly generationDate: string | null;
  readonly reviewedBy: string | null;
  readonly reviewedAt: string | null;
  readonly findings: readonly FindingView[];
  readonly createdAt: string;
}

export interface ReviewTaskDetail {
  readonly task: ReviewTaskView;
  readonly variant: VariantView | null;
  readonly revision: RevisionView | null;
}

// --- variant body --------------------------------------------------------------

export const BODY_LOCALES = ['fr', 'en'] as const;
export type BodyLocale = (typeof BODY_LOCALES)[number];

/** A French and English text pair, as stored in a variant body. */
export type LocalizedText = Readonly<Record<BodyLocale, string>>;

export interface VariantOption {
  readonly text: LocalizedText;
  readonly isCorrect: boolean;
}

/** The reviewer-visible and editable parts of a variant body. */
export interface VariantBody {
  readonly prompt: LocalizedText;
  readonly options: readonly VariantOption[];
  readonly explanation: LocalizedText;
  /** The stored object, kept so an edit preserves keys the form does not show. */
  readonly raw: Readonly<Record<string, unknown>>;
}

type Json = Record<string, unknown>;

function isRecord(value: unknown): value is Json {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function localized(value: unknown): LocalizedText {
  const source = isRecord(value) ? value : {};
  const pick = (locale: BodyLocale): string =>
    typeof source[locale] === 'string' ? source[locale] : '';
  return { fr: pick('fr'), en: pick('en') };
}

export function parseVariantBody(value: unknown): VariantBody {
  const raw = isRecord(value) ? value : {};
  const options = Array.isArray(raw.options) ? (raw.options as unknown[]) : [];
  return {
    prompt: localized(raw.prompt_text),
    explanation: localized(raw.explanation),
    options: options.map((option) => {
      const o = isRecord(option) ? option : {};
      return { text: localized(o.text), isCorrect: o.is_correct === true };
    }),
    raw,
  };
}

/** The body to POST to `/edit`: the stored object with the edited texts applied. */
export function buildEditedBody(edited: VariantBody): Json {
  const storedOptions = Array.isArray(edited.raw.options) ? (edited.raw.options as unknown[]) : [];
  return {
    ...edited.raw,
    prompt_text: {
      ...(isRecord(edited.raw.prompt_text) ? edited.raw.prompt_text : {}),
      ...edited.prompt,
    },
    explanation: {
      ...(isRecord(edited.raw.explanation) ? edited.raw.explanation : {}),
      ...edited.explanation,
    },
    options: edited.options.map((option, index) => {
      const stored = isRecord(storedOptions[index]) ? storedOptions[index] : {};
      const storedText = isRecord(stored.text) ? stored.text : {};
      return { ...stored, text: { ...storedText, ...option.text } };
    }),
  };
}

/** True when the edited texts differ from the stored ones. */
export function isBodyChanged(original: VariantBody, edited: VariantBody): boolean {
  return JSON.stringify(buildEditedBody(original)) !== JSON.stringify(buildEditedBody(edited));
}

/** True when every French and English text is non-blank. */
export function isBodyComplete(body: VariantBody): boolean {
  const texts = [
    ...BODY_LOCALES.map((l) => body.prompt[l]),
    ...BODY_LOCALES.map((l) => body.explanation[l]),
    ...body.options.flatMap((o) => BODY_LOCALES.map((l) => o.text[l])),
  ];
  return texts.every((text) => text.trim().length > 0);
}

// --- errors --------------------------------------------------------------------

export const COMMENT_MAX_CHARS = 2000;

export type ReviewerErrorKind =
  'expertise' | 'notFound' | 'alreadyDecided' | 'invalid' | 'unauthenticated' | 'generic';

/** Thrown by the API adapter; carries only a classification and the translated detail. */
export class ReviewerApiError extends Error {
  constructor(
    readonly detail: string,
    readonly status: number,
    readonly problemType: string = '',
  ) {
    super(detail);
    this.name = 'ReviewerApiError';
  }

  get kind(): ReviewerErrorKind {
    return classifyReviewerError(this.status, this.problemType);
  }
}

export function classifyReviewerError(status: number, problemType: string): ReviewerErrorKind {
  if (problemType.endsWith('reviewer-expertise-required')) return 'expertise';
  if (problemType.endsWith('review-task-already-decided')) return 'alreadyDecided';
  if (status === 401) return 'unauthenticated';
  if (status === 403) return 'expertise';
  if (status === 404) return 'notFound';
  if (status === 409) return 'alreadyDecided';
  if (status === 422) return 'invalid';
  return 'generic';
}

/** Validate a decision comment before sending; the API repeats the check. */
export function validateComment(
  action: DecisionAction,
  comment: string,
): 'required' | 'tooLong' | null {
  const text = comment.trim();
  if (action === 'requestChanges' && text.length === 0) return 'required';
  if (text.length > COMMENT_MAX_CHARS) return 'tooLong';
  return null;
}

// --- port ----------------------------------------------------------------------

export interface ReviewerApi {
  list(cursor?: string | null): Promise<ReviewTaskPage>;
  get(taskId: string): Promise<ReviewTaskDetail>;
  decide(action: DecisionAction, taskId: string, comment: string): Promise<ReviewTaskView>;
  /** Returns the new task opened for the edited revision. */
  edit(taskId: string, body: Readonly<Record<string, unknown>>): Promise<ReviewTaskView>;
}
