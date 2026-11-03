// Pure logic and port for the "Report a problem" control (ai-content Req 18.1).
//
// The categories and the comment limit mirror `PROBLEM_REPORT_CATEGORIES` and
// `PROBLEM_REPORT_COMMENT_MAX_CHARS` in `edu_it_domain/config.py`; the API stays
// authoritative and answers a 422 problem document for anything outside them.

export const PROBLEM_REPORT_CATEGORIES = [
  'incorrect',
  'unclear',
  'translation',
  'outdated',
  'technical',
  'other',
] as const;

export type ProblemReportCategory = (typeof PROBLEM_REPORT_CATEGORIES)[number];

export const PROBLEM_REPORT_COMMENT_MAX_CHARS = 1000;

export type ProblemReportItemKind = 'question' | 'lesson' | 'lab';

export interface ProblemReportInput {
  readonly itemKind: ProblemReportItemKind;
  readonly itemId: string;
  readonly contentVersion: string;
  readonly category: ProblemReportCategory;
  /** Trimmed free text, absent when empty. */
  readonly comment?: string;
}

export interface ProblemReportResult {
  readonly stableId: string;
  readonly replayed: boolean;
}

export interface ProblemReportApi {
  submit(input: ProblemReportInput, idempotencyKey: string): Promise<ProblemReportResult>;
}

/** A non-2xx answer; `message` is the translated learner-facing detail, possibly empty. */
export class ProblemReportApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ProblemReportApiError';
    this.status = status;
  }
}

export function isProblemReportCategory(value: string): value is ProblemReportCategory {
  return (PROBLEM_REPORT_CATEGORIES as readonly string[]).includes(value);
}

/** The comment as sent: trimmed, `undefined` when empty. */
export function normaliseComment(raw: string): string | undefined {
  const text = raw.trim();
  return text.length > 0 ? text : undefined;
}
