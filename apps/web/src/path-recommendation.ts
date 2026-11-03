// Types and port for the dashboard path recommendation card (ai-content Req 17.7, 17.8).
//
// The API (`GET /path-recommendation`, `POST /path-recommendation/{id}/dismissal`) stays
// authoritative: it filters the stored order against current eligibility at read time.

import { z } from 'zod';

export const pathRecommendationSchema = z.object({
  stable_id: z.string().min(1),
  source: z.string(),
  ordering: z.array(z.string()),
  created_at: z.string(),
});

export type PathRecommendation = z.infer<typeof pathRecommendationSchema>;

/** The advisor sources the card words differently; anything else gets the neutral wording. */
export const SOURCE_LLM = 'llm';
export const SOURCE_RULE_BASED = 'rule-based';

export interface PathRecommendationApi {
  /** The latest undismissed recommendation, or `null` when the API answers 204. */
  get(): Promise<PathRecommendation | null>;
  /** Dismiss it; repeating the call is safe. Rejects with `PathRecommendationApiError`. */
  dismiss(stableId: string): Promise<void>;
}

/** A non-2xx or unreadable answer. */
export class PathRecommendationApiError extends Error {
  readonly status: number;

  constructor(status: number) {
    super(`Path recommendation request failed (${status})`);
    this.name = 'PathRecommendationApiError';
    this.status = status;
  }
}
