// Adapters binding the generated OpenAPI client to the practice ports
// (quiz player, review session, project submission; Req 19.4, 19.5).
//
// All requests flow through `GeneratedClient.call` (Req 16.3); no hand-written
// fetch code lives in the pages. Non-2xx responses (RFC 9457 Problem_Details)
// become a `PracticeApiError` carrying only the translated learner-facing
// `detail`, never internal diagnostics.

import { GeneratedClient } from './generated/api-client.js';
import { routing } from './i18n/routing.js';
import { isLocale, type Locale } from './locale.js';
import {
  PracticeApiError,
  type HintResult,
  type ProjectApi,
  type ProjectSubmissionInput,
  type ProjectSubmissionResult,
  type ProjectView,
  type QuizApi,
  type QuizAttemptResult,
  type QuizView,
  type ReviewApi,
  type ReviewQuestionView,
  type ReviewResult,
} from './practice.js';

function apiBaseUrl(): string {
  const fromEnv = process.env.NEXT_PUBLIC_API_BASE_URL;
  return fromEnv && fromEnv.length > 0 ? fromEnv : 'http://localhost:8000/v1';
}

function resolveLocale(locale: string): Locale {
  return isLocale(locale) ? locale : routing.defaultLocale;
}

type Json = Record<string, unknown>;

async function readJson(response: Response): Promise<Json> {
  if (!response.ok) {
    let detail = '';
    try {
      const body = (await response.json()) as { detail?: unknown; title?: unknown };
      if (typeof body.detail === 'string') detail = body.detail;
      else if (typeof body.title === 'string') detail = body.title;
    } catch {
      // Non-JSON error body: no learner-facing detail to surface.
    }
    throw new PracticeApiError(detail, response.status);
  }
  return (await response.json()) as Json;
}

function str(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function num(value: unknown): number {
  return typeof value === 'number' ? value : 0;
}

function list(value: unknown): Json[] {
  return Array.isArray(value) ? (value as Json[]) : [];
}

interface Requester {
  call(method: string, path: string, init?: RequestInit): Promise<Response>;
}

function requester(client?: GeneratedClient): Requester {
  return client ?? new GeneratedClient({ baseUrl: apiBaseUrl() });
}

function jsonInit(locale: Locale, body: unknown, extra: Record<string, string> = {}): RequestInit {
  return {
    credentials: 'include',
    headers: { 'content-type': 'application/json', 'accept-language': locale, ...extra },
    body: JSON.stringify(body),
  };
}

function mapOptions(raw: unknown): { optionId: number; text: string }[] {
  return list(raw).map((o) => ({ optionId: num(o.option_id), text: str(o.text) }));
}

export function createQuizApi(localeInput: string, client?: GeneratedClient): QuizApi {
  const locale = resolveLocale(localeInput);
  const http = requester(client);
  return {
    async attempt(quizId, selections, options): Promise<QuizAttemptResult> {
      const body = await readJson(
        await http.call(
          'POST',
          '/quiz-attempts',
          jsonInit(
            locale,
            {
              quiz_id: quizId,
              selections,
              ...(options.contentVersion !== undefined
                ? { content_version: options.contentVersion }
                : {}),
            },
            { 'idempotency-key': options.idempotencyKey },
          ),
        ),
      );
      const attempt = (body.attempt ?? {}) as Json;
      const best = (body.best_attempt ?? {}) as Json;
      const counted: Record<string, boolean> = {};
      for (const answer of list(body.answers)) {
        counted[str(answer.question_id)] = answer.counted_for_mastery === true;
      }
      return {
        score: num(attempt.score),
        bestScore: num(best.score),
        replayed: body.replayed === true,
        countedForMastery: counted,
        mastery: list(body.mastery).map((m) => ({
          conceptId: str(m.concept_id),
          score: num(m.score),
          state: str(m.state),
        })),
      };
    },
    async revealHint(quizId, questionId): Promise<HintResult> {
      const body = await readJson(
        await http.call(
          'POST',
          '/hints',
          jsonInit(locale, { quiz_id: quizId, question_id: questionId }),
        ),
      );
      return { questionId: str(body.question_id), hintLevel: num(body.hint_level) };
    },
  };
}

export function createReviewApi(localeInput: string, client?: GeneratedClient): ReviewApi {
  const locale = resolveLocale(localeInput);
  const http = requester(client);
  return {
    async submit(conceptId, selections): Promise<ReviewResult> {
      const body = await readJson(
        await http.call(
          'POST',
          '/reviews',
          jsonInit(locale, { concept_id: conceptId, selections }),
        ),
      );
      return {
        passed: body.passed === true,
        score: num(body.score),
        dueAt: str(body.due_at),
        intervalSeconds: num(body.interval_seconds),
      };
    },
  };
}

export function createProjectApi(localeInput: string, client?: GeneratedClient): ProjectApi {
  const locale = resolveLocale(localeInput);
  const http = requester(client);
  return {
    async submit(input: ProjectSubmissionInput): Promise<ProjectSubmissionResult> {
      const body = await readJson(
        await http.call(
          'POST',
          '/project-submissions',
          jsonInit(locale, {
            project_id: input.projectId,
            self_assessment: input.selfAssessment,
            evidence: input.evidence,
          }),
        ),
      );
      return {
        stableId: str(body.stable_id),
        status: body.status === 'reviewed' ? 'reviewed' : 'pending',
        reviewOutcome: typeof body.review_outcome === 'string' ? body.review_outcome : null,
      };
    },
  };
}

// --- server-side content loaders ----------------------------------------------
//
// The learning API publishes no typed quiz/project content operations yet, so
// these loaders use the generated client's `call` surface against the content
// read routes. A missing resource yields `null` (rendered as "unavailable").

export async function loadQuiz(
  quizId: string,
  localeInput: string,
  cookie?: string,
  client?: GeneratedClient,
): Promise<QuizView | null> {
  const locale = resolveLocale(localeInput);
  const response = await requester(client).call(
    'GET',
    `/quizzes/${encodeURIComponent(quizId)}?locale=${locale}`,
    cookie ? { headers: { cookie } } : undefined,
  );
  if (!response.ok) return null;
  const body = (await response.json()) as Json;
  return {
    stableId: str(body.stable_id),
    title: str(body.title),
    ...(typeof body.content_version === 'string' ? { contentVersion: body.content_version } : {}),
    questions: list(body.questions).map((q) => ({
      stableId: str(q.stable_id),
      prompt: str(q.prompt),
      options: mapOptions(q.options),
      hints: Array.isArray(q.hints) ? (q.hints as unknown[]).map(str) : [],
    })),
  };
}

export async function loadReviewQuestions(
  conceptId: string,
  localeInput: string,
  cookie?: string,
  client?: GeneratedClient,
): Promise<readonly ReviewQuestionView[] | null> {
  const locale = resolveLocale(localeInput);
  const response = await requester(client).call(
    'GET',
    `/reviews?concept_id=${encodeURIComponent(conceptId)}`,
    { headers: { 'accept-language': locale, ...(cookie ? { cookie } : {}) } },
  );
  if (!response.ok) return null;
  const body = (await response.json()) as Json;
  return list(body.questions).map((q) => ({
    stableId: str(q.stable_id),
    prompt: str(q.prompt),
    options: mapOptions(q.options),
  }));
}

export async function loadProject(
  projectId: string,
  localeInput: string,
  cookie?: string,
  client?: GeneratedClient,
): Promise<ProjectView | null> {
  const locale = resolveLocale(localeInput);
  const response = await requester(client).call(
    'GET',
    `/projects/${encodeURIComponent(projectId)}?locale=${locale}`,
    cookie ? { headers: { cookie } } : undefined,
  );
  if (!response.ok) return null;
  const body = (await response.json()) as Json;
  return {
    stableId: str(body.stable_id),
    title: str(body.title),
    description: str(body.description),
    rubric: list(body.rubric).map((c) => ({
      stableId: str(c.stable_id),
      title: str(c.title),
      maxPoints: num(c.max_points),
    })),
  };
}
