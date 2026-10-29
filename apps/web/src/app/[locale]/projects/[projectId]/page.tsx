// Project submission page under `/fr/projects/[projectId]` and
// `/en/projects/[projectId]` (Req 19.5, 19.6, 20).

import type { Metadata } from 'next';
import { getTranslations } from 'next-intl/server';

import { ProjectSubmission } from '../../../../components/project-submission.js';
import { loadProject } from '../../../../practice-api.js';
import { practiceMetadata, requirePracticeContext } from '../../practice-page.js';

export const dynamic = 'force-dynamic';

interface ProjectPageProps {
  params: Promise<{ locale: string; projectId: string }>;
}

export function generateMetadata({ params }: ProjectPageProps): Promise<Metadata> {
  return practiceMetadata(params, 'project');
}

export default async function ProjectPage({ params }: ProjectPageProps) {
  const { locale: rawLocale, projectId } = await params;
  const { locale, cookie } = await requirePracticeContext(rawLocale);
  const project = await loadProject(projectId, locale, cookie).catch(() => null);
  if (project === null) {
    const t = await getTranslations({ locale, namespace: 'Practice.project' });
    return <p role="alert">{t('unavailable')}</p>;
  }
  return <ProjectSubmission locale={locale} project={project} />;
}
