// Lesson fetch for the lesson reader, through the generated client (Req 16.3).
import { GeneratedClient } from './generated/api-client.js';

/** A lesson as served by `GET /v1/lessons/{stable_id}`. */
export interface Lesson {
  stable_id: string;
  module_id: string;
  quiz_id: string | null;
  title: string;
  body: string;
  locale: string;
  content_version: string;
}

function apiBaseUrl(): string {
  const fromEnv = process.env.NEXT_PUBLIC_API_BASE_URL;
  return fromEnv && fromEnv.length > 0 ? fromEnv : 'http://localhost:8000/v1';
}

function isLesson(value: unknown): value is Lesson {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  const v = value as Record<string, unknown>;
  return (
    typeof v.stable_id === 'string' && typeof v.title === 'string' && typeof v.body === 'string'
  );
}

/** Fetch a lesson in `locale` (API falls back to English). Returns undefined when absent. */
export async function fetchLesson(
  lessonId: string,
  locale: string,
  client?: GeneratedClient,
): Promise<Lesson | undefined> {
  const generated = client ?? new GeneratedClient({ baseUrl: apiBaseUrl() });
  const response = await generated.call(
    'GET',
    `/lessons/${encodeURIComponent(lessonId)}?locale=${encodeURIComponent(locale)}`,
    { headers: { accept: 'application/json', 'accept-language': locale }, cache: 'no-store' },
  );
  if (!response.ok) {
    return undefined;
  }
  const body: unknown = await response.json();
  return isLesson(body) ? body : undefined;
}
