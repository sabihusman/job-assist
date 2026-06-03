import { describe, expect, test } from 'vitest';

import {
  PIPELINE_STAGES,
  type PipelineStage,
  STAGE_LABELS,
  STAGE_SORT_ORDER,
  sanitizeColumnOrder,
  stageBadgeTone,
  stageOf,
} from '@/lib/applied/stages';

describe('sanitizeColumnOrder', () => {
  test('a partial order is preserved then completed', () => {
    const out = sanitizeColumnOrder(['rejected', 'applied']);
    expect(out[0]).toBe('rejected');
    expect(out[1]).toBe('applied');
    expect(out).toHaveLength(PIPELINE_STAGES.length);
    expect(new Set(out).size).toBe(PIPELINE_STAGES.length);
  });

  test('drops unknown keys and appends missing stages', () => {
    const out = sanitizeColumnOrder(['offer', 'bogus', 'applied', null, undefined]);
    expect(out.slice(0, 2)).toEqual(['offer', 'applied']);
    expect(out).toHaveLength(PIPELINE_STAGES.length);
    expect(out).toContain('ghosted'); // a missing stage got appended
    expect(out).not.toContain('bogus' as unknown as PipelineStage);
  });

  test('drops duplicates', () => {
    const out = sanitizeColumnOrder(['applied', 'applied', 'rejected']);
    expect(out.filter((s) => s === 'applied')).toHaveLength(1);
    expect(out).toHaveLength(PIPELINE_STAGES.length);
  });

  test('null / undefined / empty yields the canonical order', () => {
    expect(sanitizeColumnOrder(null)).toEqual([...PIPELINE_STAGES]);
    expect(sanitizeColumnOrder(undefined)).toEqual([...PIPELINE_STAGES]);
    expect(sanitizeColumnOrder([])).toEqual([...PIPELINE_STAGES]);
  });
});

describe('stageOf', () => {
  test('null / unknown values return null', () => {
    expect(stageOf(null)).toBeNull();
    expect(stageOf(undefined)).toBeNull();
    expect(stageOf('')).toBeNull();
    expect(stageOf('unclassified')).toBeNull();
    expect(stageOf('unrelated')).toBeNull();
  });

  test('buckets each known outcome_type into the right stage', () => {
    expect(stageOf('application_confirmation')).toBe('applied');
    expect(stageOf('applied')).toBe('applied');
    expect(stageOf('recruiter_screen_invite')).toBe('recruiter');
    expect(stageOf('phone_interview_invite')).toBe('phone');
    expect(stageOf('video_interview_invite')).toBe('video');
    expect(stageOf('onsite_interview_invite')).toBe('onsite');
    expect(stageOf('panel_interview_invite')).toBe('onsite');
    expect(stageOf('offer')).toBe('offer');
    expect(stageOf('rejection_pre_screen')).toBe('rejected');
    expect(stageOf('rejection_post_interview')).toBe('rejected');
    expect(stageOf('withdrawn')).toBe('rejected');
  });
});

describe('stageBadgeTone', () => {
  test('positive for applied and offer', () => {
    expect(stageBadgeTone('applied')).toBe('positive');
    expect(stageBadgeTone('offer')).toBe('positive');
  });
  test('negative for rejected, muted for ghosted', () => {
    expect(stageBadgeTone('rejected')).toBe('negative');
    expect(stageBadgeTone('ghosted')).toBe('muted');
  });
  test('pending for interview stages', () => {
    for (const stage of ['recruiter', 'phone', 'video', 'onsite'] as PipelineStage[]) {
      expect(stageBadgeTone(stage)).toBe('pending');
    }
  });
});

describe('STAGE_SORT_ORDER + STAGE_LABELS', () => {
  test('offer ranks before rejected before ghosted', () => {
    expect(STAGE_SORT_ORDER.offer).toBeLessThan(STAGE_SORT_ORDER.rejected);
    expect(STAGE_SORT_ORDER.rejected).toBeLessThan(STAGE_SORT_ORDER.ghosted);
  });
  test('every stage has a label', () => {
    for (const stage of [
      'applied',
      'recruiter',
      'phone',
      'video',
      'onsite',
      'offer',
      'rejected',
      'ghosted',
    ] as const) {
      expect(STAGE_LABELS[stage]).toBeTruthy();
    }
  });
});
