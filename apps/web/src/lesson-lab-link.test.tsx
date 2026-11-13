// Lesson page lab link (hands-on-labs task 16.3, Req 15.12 / task 16.2).
//
// A lesson with `lab_ids` links to the Lab_View; the former "coming soon"
// placeholder is gone. The async Server Component is awaited and rendered to
// static markup, with only the network, MDX, and Next.js plumbing stubbed.
import type { ReactNode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createTranslator } from 'next-intl';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import en from './messages/en.json';
import fr from './messages/fr.json';
import type { Lesson } from './lesson-api.js';

const BODY = 'lesson body';
const fetchLesson = vi.hoisted(() => vi.fn());

vi.mock('./lesson-api.js', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./lesson-api.js')>()),
  fetchLesson,
}));
vi.mock('./learning-server.js', () => ({
  requireSessionReference: async () => 'session-ref',
}));
vi.mock('./components/lesson-mdx.js', () => ({
  renderLessonMdx: async () => <p>{BODY}</p>,
}));
vi.mock('next/link', () => ({
  default: ({ href, children }: { href: string; children?: ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));
vi.mock('next/navigation', () => ({
  notFound: () => {
    throw new Error('not found');
  },
}));
vi.mock('next-intl/server', () => ({
  setRequestLocale: () => undefined,
  getTranslations: async ({ locale, namespace }: { locale: 'en' | 'fr'; namespace: string }) =>
    createTranslator({
      locale,
      messages: locale === 'fr' ? fr : en,
      namespace: namespace as 'Lesson',
    }),
}));

import LessonPage from './app/[locale]/tracks/[trackId]/modules/[moduleId]/lessons/[lessonId]/page.js';

function lesson(labIds: string[], quizId: string | null = null): Lesson {
  return {
    title: 'Shell basics',
    locale: 'en',
    body: 'x',
    quiz_id: quizId,
    lab_ids: labIds,
  } as unknown as Lesson;
}

async function render(
  locale: 'en' | 'fr',
  labIds: string[],
  quizId: string | null = null,
): Promise<string> {
  fetchLesson.mockResolvedValue(lesson(labIds, quizId));
  const element = await LessonPage({
    params: Promise.resolve({ locale, trackId: 't', moduleId: 'm', lessonId: 'l' }),
  });
  return renderToStaticMarkup(element);
}

describe('lesson page lab links', () => {
  beforeEach(() => fetchLesson.mockReset());

  it('links each lab to its Lab_View and shows no "coming soon" placeholder', async () => {
    const html = await render('en', ['lab-1', 'lab 2']);
    expect(html).toContain('href="/en/labs/lab-1"');
    expect(html).toContain('href="/en/labs/lab%202"');
    expect(html).toContain('Open lab 1');
    expect(html).toContain('Open lab 2');
    expect(html).not.toMatch(/coming soon|bientôt|placeholder/i);
  });

  it('uses the active locale for the link and its label', async () => {
    const html = await render('fr', ['lab-1']);
    expect(html).toContain('href="/fr/labs/lab-1"');
    expect(html).toContain('Ouvrir le labo 1');
  });

  it('renders no labs section when the lesson has no labs', async () => {
    const html = await render('en', []);
    expect(html).not.toContain('lesson-labs-heading');
    expect(html).not.toContain('/labs/');
  });

  it('fetches the lesson with the visitor session reference', async () => {
    await render('en', []);
    expect(fetchLesson).toHaveBeenCalledWith('l', 'en', undefined, 'session-ref');
  });

  it('links to the lesson quiz when it has one, and to nothing otherwise', async () => {
    const withQuiz = await render('en', [], 'quiz 1');
    expect(withQuiz).toContain('href="/en/quizzes/quiz%201"');
    expect(withQuiz).toContain('Take the quiz');
    expect(await render('en', [])).not.toContain('/quizzes/');
    expect(await render('fr', [], 'quiz-1')).toContain('Passer le quiz');
  });

  it('links back to the module in the active locale', async () => {
    const html = await render('fr', []);
    expect(html).toContain('href="/fr/tracks/t/modules/m"');
    expect(html).toContain('Retour au module');
  });

  it('names a lab by its title when the API provides one', async () => {
    fetchLesson.mockResolvedValue({
      ...lesson(['lab-1', 'lab-2']),
      labs: [
        { lab_id: 'lab-1', title: 'Fix the shared folder' },
        { lab_id: 'lab-2', title: '' },
      ],
    });
    const element = await LessonPage({
      params: Promise.resolve({ locale: 'fr', trackId: 't', moduleId: 'm', lessonId: 'l' }),
    });
    const html = renderToStaticMarkup(element);
    expect(html).toContain('Labo : Fix the shared folder');
    expect(html).toContain('Ouvrir le labo 2'); // untitled lab keeps the numbered label
  });
});
