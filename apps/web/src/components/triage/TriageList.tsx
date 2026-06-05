'use client';

import { useEffect, useRef } from 'react';

import { TriageCard, type TriageCardAction } from '@/components/triage/TriageCard';
import type { PostingListItem } from '@/lib/triage/types';

/**
 * Container for the card list. Receives the lifted `selectedIndex`
 * from TriagePage (so the keyboard hook lives at page level, not
 * here) and scrolls the active card into view whenever the index
 * changes.
 *
 * J/K logic lives in `useTriageKeyboard` rather than here so the
 * Escape-clears-selection behavior can share state with the detail
 * panel.
 */
export function TriageList({
  postings,
  selectedIndex,
  reasonPickerCardId,
  selectedIds,
  onSelect,
  onToggleReason,
  onAction,
  onToggleSelect,
}: {
  postings: readonly PostingListItem[];
  selectedIndex: number | null;
  /** PR #47: which card's reason picker is currently expanded.
   *  ``null`` = no picker open. Threaded down so the page-level keyboard
   *  handler can flip it on the ``2`` chord. */
  reasonPickerCardId: string | null;
  /** feat/bulk-triage-actions: the set of checkbox-selected posting ids.
   *  Undefined disables the per-card checkbox entirely. */
  selectedIds?: Set<string>;
  onSelect: (index: number) => void;
  /** PR #47: toggle the reason picker open/closed for one card. */
  onToggleReason: (postingId: string) => void;
  onAction: (postingId: string, action: TriageCardAction) => void;
  /** feat/bulk-triage-actions: toggle one posting's checkbox membership. */
  onToggleSelect?: (postingId: string) => void;
}) {
  const containerRef = useRef<HTMLUListElement>(null);

  // Scroll the active card into view when the index changes. `nearest`
  // avoids the page-jump feel of `center` while still keeping the card
  // visible during J/K nav.
  useEffect(() => {
    if (selectedIndex === null) return;
    const card = containerRef.current?.querySelector<HTMLElement>(
      `[data-card-index="${selectedIndex}"]`,
    );
    card?.scrollIntoView({ block: 'nearest' });
  }, [selectedIndex]);

  return (
    <ul ref={containerRef} className="flex list-none flex-col gap-3 p-0">
      {postings.map((posting, index) => (
        <li key={posting.id} data-card-index={index}>
          <TriageCard
            posting={posting}
            isSelected={selectedIndex === index}
            reasonOpen={reasonPickerCardId === posting.id}
            isChecked={selectedIds?.has(posting.id) ?? false}
            onSelect={() => onSelect(index)}
            onToggleReason={() => onToggleReason(posting.id)}
            onAction={(action) => onAction(posting.id, action)}
            onToggleCheck={onToggleSelect ? () => onToggleSelect(posting.id) : undefined}
          />
        </li>
      ))}
    </ul>
  );
}
