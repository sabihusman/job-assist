import { describe, expect, test } from 'vitest';

import { buildCsv, csvCell } from '@/lib/csv';

describe('csvCell', () => {
  test('passes plain values through unquoted', () => {
    expect(csvCell('Acme')).toBe('Acme');
    expect(csvCell('123')).toBe('123');
  });

  test('quotes cells containing commas, quotes, CR or LF and escapes quotes', () => {
    expect(csvCell('Acme, Inc.')).toBe('"Acme, Inc."');
    expect(csvCell('He said "hi"')).toBe('"He said ""hi"""');
    expect(csvCell('line1\nline2')).toBe('"line1\nline2"');
    expect(csvCell('line1\rline2')).toBe('"line1\rline2"');
  });
});

describe('buildCsv', () => {
  test('joins header + rows with CRLF, stringifies cells, renders nullish empty', () => {
    const csv = buildCsv(
      ['a', 'b', 'c'],
      [
        ['x', 1, null],
        ['y, z', undefined, 0],
      ],
    );
    expect(csv).toBe('a,b,c\r\nx,1,\r\n"y, z",,0');
  });

  test('empty rows → header only (valid CSV, not an error)', () => {
    expect(buildCsv(['a', 'b'], [])).toBe('a,b');
  });

  test('is BOM-free (the download path owns the BOM)', () => {
    expect(buildCsv(['a'], [['x']]).charCodeAt(0)).not.toBe(0xfeff);
  });
});
