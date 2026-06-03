import { beforeEach, describe, expect, test } from 'vitest';

import { PIPELINE_STAGES } from '@/lib/applied/stages';
import { useUiStore } from '@/lib/stores/ui';

describe('useUiStore — pipeline column order', () => {
  beforeEach(() => {
    localStorage.clear();
    useUiStore.getState().resetPipelineColumnOrder();
  });

  test('default is the canonical PIPELINE_STAGES order', () => {
    expect(useUiStore.getState().pipelineColumnOrder).toEqual([...PIPELINE_STAGES]);
  });

  test("movePipelineColumn('down') swaps a column with the next one", () => {
    const [first, second] = useUiStore.getState().pipelineColumnOrder;
    useUiStore.getState().movePipelineColumn(first, 'down');
    const after = useUiStore.getState().pipelineColumnOrder;
    expect(after[0]).toBe(second);
    expect(after[1]).toBe(first);
    // still a complete permutation
    expect(new Set(after).size).toBe(PIPELINE_STAGES.length);
  });

  test('moving the first column up is a no-op (bounds)', () => {
    const before = [...useUiStore.getState().pipelineColumnOrder];
    useUiStore.getState().movePipelineColumn(before[0], 'up');
    expect(useUiStore.getState().pipelineColumnOrder).toEqual(before);
  });

  test('the order persists to localStorage under job-assist:ui (partialize)', () => {
    useUiStore.getState().movePipelineColumn(PIPELINE_STAGES[0], 'down');
    const persisted = JSON.parse(localStorage.getItem('job-assist:ui') ?? '{}');
    expect(persisted.state.pipelineColumnOrder[0]).toBe(PIPELINE_STAGES[1]);
    // sidebarCollapsed is also persisted; palette/mobile are not.
    expect(persisted.state).not.toHaveProperty('paletteOpen');
  });

  test('a stale/partial persisted order is repaired by movePipelineColumn (sanitize)', () => {
    // Simulate a stale store value with a bogus + missing key.
    useUiStore.setState({
      pipelineColumnOrder: ['offer', 'bogus', 'applied'] as never,
    });
    useUiStore.getState().movePipelineColumn('applied', 'up');
    const order = useUiStore.getState().pipelineColumnOrder;
    expect(order).toHaveLength(PIPELINE_STAGES.length);
    expect(order).not.toContain('bogus');
    expect(new Set(order).size).toBe(PIPELINE_STAGES.length);
  });
});
