import { describe, expect, test } from 'vitest';

import type { PipelineStage } from '@/lib/applied/stages';
import { type ApplicationCard, emptyBuckets } from '@/lib/pipeline/bucket';
import { buildPipelineCsv } from '@/lib/pipeline/exportCsv';

function card(companyName: string, roleTitle: string, appliedAt: string): ApplicationCard {
  return { id: `${companyName}-${roleTitle}`, companyName, roleTitle, roleFamily: null, appliedAt };
}

describe('buildPipelineCsv', () => {
  test('empty board → header row only', () => {
    const csv = buildPipelineCsv(emptyBuckets(), ['applied', 'rejected']);
    expect(csv).toBe('stage,company,role,date');
  });

  test('rows follow the column order, with the human stage label', () => {
    const b = emptyBuckets();
    b.rejected.push(card('Acme', 'PM', '2026-01-02T00:00:00Z'));
    b.applied.push(card('Beta', 'Sr PM', '2026-01-01T00:00:00Z'));

    const order: PipelineStage[] = ['applied', 'rejected'];
    const lines = buildPipelineCsv(b, order).split('\r\n');

    expect(lines[0]).toBe('stage,company,role,date');
    // "applied" stage renders as "Still Alive" and comes first per the order.
    expect(lines[1]).toBe('Still Alive,Beta,Sr PM,2026-01-01T00:00:00Z');
    expect(lines[2]).toBe('Rejected,Acme,PM,2026-01-02T00:00:00Z');
    expect(lines).toHaveLength(3);
  });

  test('stages absent from the order are not exported', () => {
    const b = emptyBuckets();
    b.offer.push(card('Skipped', 'PM', '2026-01-01T00:00:00Z'));
    b.applied.push(card('Kept', 'PM', '2026-01-01T00:00:00Z'));
    const csv = buildPipelineCsv(b, ['applied']); // offer omitted
    expect(csv).toContain('Kept');
    expect(csv).not.toContain('Skipped');
  });

  test('RFC-4180 escapes commas, quotes, and embedded newlines', () => {
    const b = emptyBuckets();
    b.applied.push(card('Acme, Inc.', 'Say "hi"\nnow', '2026-01-01T00:00:00Z'));
    const csv = buildPipelineCsv(b, ['applied']);
    // The \n lives INSIDE the quoted cell, so it is NOT a record separator
    // (records join on \r\n) — header + exactly one data record.
    const expectedRow = 'Still Alive,"Acme, Inc.","Say ""hi""\nnow",2026-01-01T00:00:00Z';
    expect(csv).toBe(`stage,company,role,date\r\n${expectedRow}`);
    expect(csv.split('\r\n')).toHaveLength(2);
  });
});
