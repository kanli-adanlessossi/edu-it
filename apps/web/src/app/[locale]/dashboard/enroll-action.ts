'use server';

// Enroll form submission: Zod-validated, then sent through the generated client.
import { redirect } from 'next/navigation';

import { requireLearningApi } from '../../../learning-server.js';
import { validateEnrollForm } from '../../../learning.js';
import { isLocale } from '../../../locale.js';

export async function enrollAction(formData: FormData): Promise<void> {
  const locale = String(formData.get('locale') ?? '');
  if (!isLocale(locale)) redirect('/fr/dashboard');

  const result = validateEnrollForm({ trackId: formData.get('trackId') });
  if (!result.ok) redirect(`/${locale}/dashboard?error=invalid`);

  const api = await requireLearningApi(locale);
  try {
    await api.enroll(result.trackId);
  } catch {
    redirect(`/${locale}/dashboard?error=enroll`);
  }
  redirect(`/${locale}/tracks/${encodeURIComponent(result.trackId)}`);
}
