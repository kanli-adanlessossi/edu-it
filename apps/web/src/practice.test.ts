import { describe, expect, it } from 'vitest';

import { buildSelections, canRevealHint, validateProjectForm, visibleHints } from './practice.js';

const project = {
  stableId: 'proj-1',
  rubric: [
    { stableId: 'c1', title: 'A', maxPoints: 5 },
    { stableId: 'c2', title: 'B', maxPoints: 3 },
  ],
};

describe('validateProjectForm', () => {
  it('accepts a full self-assessment with evidence', () => {
    const r = validateProjectForm(project, { c1: '5', c2: '0' }, [' https://x.test ', '']);
    expect(r).toEqual({
      ok: true,
      input: {
        projectId: 'proj-1',
        selfAssessment: { c1: 5, c2: 0 },
        evidence: ['https://x.test'],
      },
    });
  });

  it('rejects a missing criterion, out-of-range points, and absent evidence', () => {
    const r = validateProjectForm(project, { c1: '9' }, ['  ']);
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.errors).toEqual([
        { code: 'criterionOutOfRange', criterionId: 'c1' },
        { code: 'criterionMissing', criterionId: 'c2' },
        { code: 'evidenceRequired' },
      ]);
    }
  });
});

describe('quiz helpers', () => {
  it('sends explicit null for unanswered questions', () => {
    expect(buildSelections(['q1', 'q2'], { q1: 2 })).toEqual({ q1: 2, q2: null });
  });

  it('reveals hints progressively and never past the last level', () => {
    const hints = ['h1', 'h2'];
    expect(visibleHints(hints, 0)).toEqual([]);
    expect(visibleHints(hints, 1)).toEqual(['h1']);
    expect(visibleHints(hints, 5)).toEqual(['h1', 'h2']);
    expect(canRevealHint(hints, 1)).toBe(true);
    expect(canRevealHint(hints, 2)).toBe(false);
  });
});
