// Lesson fetch for the lesson reader, through the generated client (Req 16.3).
import { createApiClient } from './create-api-client.js';
import type { GeneratedClient } from './generated/api-client.js';
import { SESSION_COOKIE } from './session-shared.js';

/** A lesson as served by `GET /v1/lessons/{stable_id}`. */
export interface Lesson {
  stable_id: string;
  module_id: string;
  quiz_id: string | null;
  title: string;
  body: string;
  locale: string;
  content_version: string;
  /** Labs the lesson references; the reader links to the Lab_View when non-empty. */
  lab_ids?: string[];
  /** The referenced labs with their localized titles (empty title when untitled). */
  labs?: { lab_id: string; title: string }[];
}

/**
 * Remove a leading level-1 heading that repeats the lesson title.
 *
 * The reader prints the title itself, and authored MDX (and the API's placeholder body for a
 * lesson without MDX) usually opens with the same `# Title`, which would show it twice. Only an
 * exact match (ignoring surrounding whitespace) is removed, so a different opening heading stays.
 */
export function stripLeadingTitle(body: string, title: string): string {
  const match = /^\s*#[ \t]+([^\n]*?)[ \t]*#*[ \t]*(?:\r?\n|$)/.exec(body);
  if (match === null || match[1]?.trim() !== title.trim()) {
    return body;
  }
  return body.slice(match[0].length).replace(/^\s*\n/, '');
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

/**
 * Fetch a lesson in `locale` (API falls back to English). Returns undefined when absent.
 *
 * The API requires a Session, so a server-side caller passes the opaque `sessionReference`,
 * which is forwarded as the session cookie; a browser caller relies on its cookie jar.
 */
export async function fetchLesson(
  lessonId: string,
  locale: string,
  client?: GeneratedClient,
  sessionReference?: string,
): Promise<Lesson | undefined> {
  const generated = client ?? createApiClient();
  const response = await generated.call(
    'GET',
    `/lessons/${encodeURIComponent(lessonId)}?locale=${encodeURIComponent(locale)}`,
    {
      headers: {
        accept: 'application/json',
        'accept-language': locale,
        ...(sessionReference === undefined
          ? {}
          : { cookie: `${SESSION_COOKIE}=${sessionReference}` }),
      },
      cache: 'no-store',
    },
  );
  if (!response.ok) {
    return undefined;
  }
  const body: unknown = await response.json();
  if (!isLesson(body)) {
    return undefined;
  }
  const labIds = Array.isArray(body.lab_ids)
    ? body.lab_ids.filter((id): id is string => typeof id === 'string')
    : [];
  const labs = Array.isArray(body.labs)
    ? body.labs.filter(
        (lab): lab is { lab_id: string; title: string } =>
          typeof lab === 'object' &&
          lab !== null &&
          typeof (lab as { lab_id?: unknown }).lab_id === 'string' &&
          typeof (lab as { title?: unknown }).title === 'string',
      )
    : [];
  return { ...body, lab_ids: labIds, labs };
}
