// "Coming soon" placeholder rendered wherever a lab is referenced
// (Req 15.7, 19.7). It deliberately takes NO props: lab titles, descriptions,
// previews, and links must never reach the markup. Callers drop it in place
// of the lab and discard all lab data.

import { useTranslations } from 'next-intl';

export function LabPlaceholder() {
  const t = useTranslations('LabPlaceholder');

  return (
    <section aria-label={t('title')} data-testid="lab-placeholder">
      <p>{t('comingSoon')}</p>
    </section>
  );
}
