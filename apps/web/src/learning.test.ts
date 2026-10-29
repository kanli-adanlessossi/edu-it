import { describe, expect, it } from 'vitest';

import {
  deriveLockState,
  parseRouteId,
  splitDueOverdue,
  unlockedPercent,
  validateEnrollForm,
} from './learning.js';

const mod = (state: string, tested_out = false, ordinal = 1) => ({
  module_id: `m${ordinal}`,
  ordinal,
  state,
  tested_out,
  completions: [],
});

describe('learning page logic', () => {
  it('derives lock state, failing closed and honouring tested-out', () => {
    expect(deriveLockState(mod('locked'))).toBe('locked');
    expect(deriveLockState(mod('unlocked'))).toBe('unlocked');
    expect(deriveLockState(mod('weird'))).toBe('locked');
    expect(deriveLockState(mod('locked', true))).toBe('unlocked');
    expect(unlockedPercent([mod('unlocked'), mod('locked', false, 2)])).toBe(50);
  });

  it('splits due and overdue purely from now', () => {
    const now = new Date('2025-01-10T00:00:00Z');
    const { due, overdue } = splitDueOverdue(
      [
        { concept_id: 'future', due_at: '2025-01-11T00:00:00Z' },
        { concept_id: 'exact', due_at: '2025-01-10T00:00:00Z' },
        { concept_id: 'late', due_at: '2025-01-09T00:00:00Z' },
        { concept_id: 'bad', due_at: 'nope' },
      ],
      now,
    );
    expect(due.map((e) => e.concept_id)).toEqual(['exact']);
    expect(overdue.map((e) => e.concept_id)).toEqual(['late']);
  });

  it('validates the enroll form and route ids with Zod', () => {
    expect(validateEnrollForm({ trackId: ' trk-1 ' })).toEqual({ ok: true, trackId: 'trk-1' });
    expect(validateEnrollForm({ trackId: '' }).ok).toBe(false);
    expect(validateEnrollForm({ trackId: 'a b/../' }).ok).toBe(false);
    expect(parseRouteId('trk-1')).toBe('trk-1');
    expect(parseRouteId('%E0%A4%A')).toBeUndefined();
  });
});
