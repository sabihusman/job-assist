import { stageOf } from '@/lib/applied/stages';
import type { OutcomeEvent } from '@/lib/applied/types';
import type { PostingListItem } from '@/lib/triage/types';

/**
 * Best-effort outcomes summary for the Companies table.
 *
 * feat/applied-company-tracking: outcomes are matched to a company by
 * `target_company_id` (the Gmail-crawl linkage), NOT by posting_id — which is
 * uniformly NULL, so the old posting-id path always rendered "—". The applied-
 * postings arg is retained only as a signal that the company has any activity.
 *
 * Returns a short phrase like "2 screens, 1 onsite" or "1 rejection", or
 * "No response yet" when applications exist but no later-stage outcomes have
 * landed, or "—" when there's no activity at all.
 */
export function summarizeOutcomes(
  companyId: string,
  _postings: readonly PostingListItem[],
  outcomes: readonly OutcomeEvent[],
): string {
  const companyOutcomes = outcomes.filter((o) => o.target_company_id === companyId);
  if (companyOutcomes.length === 0) return '—';

  let screens = 0;
  let onsite = 0;
  let offer = 0;
  let rejection = 0;

  for (const o of companyOutcomes) {
    const stage = stageOf(o.stage);
    if (!stage) continue;
    if (stage === 'recruiter' || stage === 'phone' || stage === 'video') screens += 1;
    else if (stage === 'onsite') onsite += 1;
    else if (stage === 'offer') offer += 1;
    else if (stage === 'rejected') rejection += 1;
  }

  const parts: string[] = [];
  if (offer > 0) parts.push(`${offer} offer${offer === 1 ? '' : 's'}`);
  if (onsite > 0) parts.push(`${onsite} onsite`);
  if (screens > 0) parts.push(`${screens} screen${screens === 1 ? '' : 's'}`);
  if (rejection > 0) parts.push(`${rejection} rejection${rejection === 1 ? '' : 's'}`);
  if (parts.length > 0) return parts.join(', ');
  return 'No response yet';
}

/** Count applied postings per company (for the APPLIED column). */
export function countAppliedByCompany(postings: readonly PostingListItem[]): Map<string, number> {
  const counts = new Map<string, number>();
  for (const p of postings) {
    const id = p.company.id;
    if (!id) continue;
    counts.set(id, (counts.get(id) ?? 0) + 1);
  }
  return counts;
}
