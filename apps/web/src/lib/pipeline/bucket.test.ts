import { describe, expect, test } from 'vitest';

import { emptyBuckets } from '@/lib/pipeline/bucket';

describe('emptyBuckets', () => {
  test('returns all 8 stages with empty arrays', () => {
    const b = emptyBuckets();
    expect(Object.keys(b)).toHaveLength(8);
    expect(b.applied).toEqual([]);
    expect(b.ghosted).toEqual([]);
  });
});
