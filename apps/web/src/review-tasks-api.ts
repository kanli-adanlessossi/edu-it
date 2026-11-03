// Adapter binding the generated OpenAPI client to the reviewer queue port
// (ai-content Req 15.2, 15.3). All requests flow through `GeneratedClient.call`;
// unsafe calls get the double-submit CSRF header from `createApiClient`.
// Non-2xx responses (RFC 9457 problem documents) become a `ReviewerApiError`.

import { createApiClient } from './create-api-client.js';
import type { GeneratedClient } from './generated/api-client.js';
import { routing } from './i18n/routing.js';
import { isLocale, type Locale } from './locale.js';
import {
  ReviewerApiError,
  parseVariantBody,
  type DecisionAction,
  type FindingView,
  type ReviewTaskDetail,
  type ReviewTaskPage,
  type ReviewTaskView,
  type ReviewerApi,
  type RevisionView,
  type VariantView,
} from './review-tasks.js';

type Json = Record<string, unknown>;

interface Requester {
  call(method: string, path: string, init?: RequestInit): Promise<Response>;
}

const DECISION_PATH: Readonly<Record<DecisionAction, string>> = {
  approve: 'approve',
  reject: 'reject',
  requestChanges: 'request-changes',
};

function str(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function strOrNull(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

function record(value: unknown): Json {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Json)
    : {};
}

async function readJson(response: Response): Promise<Json> {
  if (!response.ok) {
    let detail = '';
    let problemType = '';
    try {
      const body = (await response.json()) as Json;
      if (typeof body.detail === 'string') detail = body.detail;
      else if (typeof body.title === 'string') detail = body.title;
      if (typeof body.type === 'string') problemType = body.type;
    } catch {
      // Non-JSON error body: no detail to surface.
    }
    throw new ReviewerApiError(detail, response.status, problemType);
  }
  return (await response.json()) as Json;
}

export function mapTask(raw: Json): ReviewTaskView {
  return {
    stableId: str(raw.stable_id),
    kind: str(raw.kind),
    targetKind: str(raw.target_kind),
    targetStableId: str(raw.target_stable_id),
    trackId: str(raw.track_id),
    state: str(raw.state),
    createdAt: str(raw.created_at),
    revisionContentVersion: strOrNull(raw.revision_content_version),
    reason: strOrNull(raw.reason),
    comment: strOrNull(raw.comment),
    reviewerId: strOrNull(raw.reviewer_id),
    decidedAt: strOrNull(raw.decided_at),
  };
}

function mapVariant(raw: Json): VariantView {
  return {
    stableId: str(raw.stable_id),
    parentQuestionId: str(raw.parent_question_id),
    trackId: str(raw.track_id),
    state: str(raw.state),
    approvedContentVersion: strOrNull(raw.approved_content_version),
  };
}

function mapFindings(raw: unknown): FindingView[] {
  return (Array.isArray(raw) ? raw : []).map((f) => {
    const o = record(f);
    return { kind: str(o.kind), target: str(o.target), detail: str(o.detail) };
  });
}

function mapRevision(raw: Json): RevisionView {
  return {
    contentVersion: str(raw.content_version),
    state: str(raw.state),
    body: parseVariantBody(raw.body),
    origin: strOrNull(raw.origin),
    model: strOrNull(raw.model),
    promptId: strOrNull(raw.prompt_id),
    promptVersion: strOrNull(raw.prompt_version),
    generationDate: strOrNull(raw.generation_date),
    reviewedBy: strOrNull(raw.reviewed_by),
    reviewedAt: strOrNull(raw.reviewed_at),
    findings: mapFindings(raw.findings),
    createdAt: str(raw.created_at),
  };
}

export function mapDetail(raw: Json): ReviewTaskDetail {
  return {
    task: mapTask(record(raw.task)),
    variant:
      raw.variant === null || raw.variant === undefined ? null : mapVariant(record(raw.variant)),
    revision:
      raw.revision === null || raw.revision === undefined
        ? null
        : mapRevision(record(raw.revision)),
  };
}

function headers(locale: Locale, cookie?: string, json = false): Record<string, string> {
  return {
    'accept-language': locale,
    ...(json ? { 'content-type': 'application/json' } : {}),
    ...(cookie ? { cookie } : {}),
  };
}

export function createReviewerApi(
  localeInput: string,
  options: { client?: GeneratedClient; cookie?: string } = {},
): ReviewerApi {
  const locale: Locale = isLocale(localeInput) ? localeInput : routing.defaultLocale;
  const http: Requester =
    options.client ?? createApiClient(options.cookie ? { cookie: options.cookie } : {});
  const base = '/review-tasks';
  return {
    async list(cursor): Promise<ReviewTaskPage> {
      const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : '';
      const body = await readJson(
        await http.call('GET', `${base}${query}`, {
          credentials: 'include',
          headers: headers(locale, options.cookie),
        }),
      );
      return {
        items: (Array.isArray(body.items) ? body.items : []).map((t) => mapTask(record(t))),
        nextCursor: strOrNull(body.next_cursor),
      };
    },
    async get(taskId): Promise<ReviewTaskDetail> {
      return mapDetail(
        await readJson(
          await http.call('GET', `${base}/${encodeURIComponent(taskId)}`, {
            credentials: 'include',
            headers: headers(locale, options.cookie),
          }),
        ),
      );
    },
    async decide(action, taskId, comment): Promise<ReviewTaskView> {
      const text = comment.trim();
      return mapTask(
        await readJson(
          await http.call(
            'POST',
            `${base}/${encodeURIComponent(taskId)}/${DECISION_PATH[action]}`,
            {
              credentials: 'include',
              headers: headers(locale, options.cookie, true),
              body: JSON.stringify(text.length > 0 ? { comment: text } : {}),
            },
          ),
        ),
      );
    },
    async edit(taskId, body): Promise<ReviewTaskView> {
      return mapTask(
        await readJson(
          await http.call('POST', `${base}/${encodeURIComponent(taskId)}/edit`, {
            credentials: 'include',
            headers: headers(locale, options.cookie, true),
            body: JSON.stringify({ body }),
          }),
        ),
      );
    },
  };
}
