// Module list with per-module lock state (Req 19.2). Lock state is conveyed in
// text (not colour alone) so it is perceivable without sight of styling.
import { useTranslations } from 'next-intl';
import Link from 'next/link';

import { deriveLockState, sortModules, type ModuleProgress } from '../learning.js';
import { type Locale } from '../locale.js';

interface ModuleListProps {
  readonly locale: Locale;
  readonly trackId: string;
  readonly modules: readonly ModuleProgress[];
}

export function ModuleList({ locale, trackId, modules }: ModuleListProps) {
  const t = useTranslations('Learning');
  return (
    <ol>
      {sortModules(modules).map((module) => {
        const lock = deriveLockState(module);
        const href = `/${locale}/tracks/${encodeURIComponent(trackId)}/modules/${encodeURIComponent(module.module_id)}`;
        return (
          <li key={module.module_id} data-lock-state={lock}>
            <Link href={href}>{module.module_id}</Link>{' '}
            <span>{t(lock === 'locked' ? 'lock.locked' : 'lock.unlocked')}</span>
          </li>
        );
      })}
    </ol>
  );
}
