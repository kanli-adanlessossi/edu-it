// Static reviewer queue fixtures shared by the axe-core page scan
// (ai-content task 16.2, Req 15.9). Not imported by application code.
import { parseVariantBody, type ReviewTaskDetail, type ReviewTaskPage } from './review-tasks.js';

const TASK = {
  stableId: 'rt-1',
  kind: 'new_variant',
  targetKind: 'variant',
  targetStableId: 'var-1',
  trackId: 'track-1',
  state: 'open',
  createdAt: '2030-01-01T10:00:00Z',
  revisionContentVersion: 'v1',
  reason: null,
  comment: null,
  reviewerId: null,
  decidedAt: null,
} as const;

export const REVIEWER_QUEUE_FIXTURE: ReviewTaskPage = {
  items: [TASK, { ...TASK, stableId: 'rt-2', targetStableId: 'var-2' }],
  nextCursor: 'cursor-2',
};

export const REVIEWER_DETAIL_FIXTURE: ReviewTaskDetail = {
  task: TASK,
  variant: {
    stableId: 'var-1',
    parentQuestionId: 'q-9',
    trackId: 'track-1',
    state: 'pending_review',
    approvedContentVersion: null,
  },
  revision: {
    contentVersion: 'v1',
    state: 'pending_review',
    body: parseVariantBody({
      prompt_text: { fr: 'Quelle commande liste les fichiers ?', en: 'Which command lists files?' },
      explanation: { fr: 'Parce que.', en: 'Because.' },
      options: [
        { text: { fr: 'ls', en: 'ls' }, is_correct: true },
        { text: { fr: 'cd', en: 'cd' }, is_correct: false },
      ],
    }),
    origin: 'ai_generated',
    model: 'model-x',
    promptId: 'variant-gen',
    promptVersion: '3',
    generationDate: '2030-01-01T09:00:00Z',
    reviewedBy: null,
    reviewedAt: null,
    findings: [{ kind: 'duplicate', target: 'prompt', detail: 'Matches q-2' }],
    createdAt: '2030-01-01T09:00:00Z',
  },
};
