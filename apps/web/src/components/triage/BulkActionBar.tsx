'use client';

import { useState } from 'react';

import type { ActionReason } from '@/lib/triage/types';
import { cn } from '@/lib/utils';

/**
 * Bulk triage actions (feat/bulk-triage-actions).
 *
 * The triage queue floods with non-PM noise (T3 broad-ingest). This bar lets
 * the operator select that cohort and clear it in one action instead of
 * passing 75 cards one-by-one.
 *
 * Always-visible selection shortcuts:
 *   - "Select ≤ N" — the junk cohort (fit_score ≤ threshold).
 *   - "Select all visible".
 * When anything is selected, the action controls appear:
 *   - "Pass N" with a reason dropdown + a two-step CONFIRM (never a blind
 *     one-click — the classifier sometimes mislabels a real PM/PO role as
 *     Other/40, so the operator gets a beat to glance).
 *   - "Reset N" (bulk-undo — reset is append-only and reversible).
 */

const BULK_REASONS: readonly { value: ActionReason; label: string }[] = [
  { value: 'wrong_role', label: 'Wrong role type' },
  { value: 'wrong_location', label: 'Wrong location' },
  { value: 'comp_too_low', label: 'Comp too low' },
  { value: 'wrong_industry', label: 'Wrong domain / industry' },
  { value: 'wrong_stage', label: 'Wrong stage' },
  { value: 'too_senior', label: 'Too senior — level' },
  { value: 'too_junior', label: 'Too junior' },
  { value: 'just_not_feeling_it', label: 'Just not feeling it' },
];

const btn =
  'inline-flex h-7 items-center rounded-md border border-border bg-surface-2 px-2.5 text-[12px] hover:bg-accent disabled:opacity-50';

export function BulkActionBar({
  selectedCount,
  visibleCount,
  lowScoreCount,
  lowScoreThreshold = 40,
  busy = false,
  onSelectLowScore,
  onSelectAllVisible,
  onClear,
  onPass,
  onReset,
}: {
  selectedCount: number;
  visibleCount: number;
  lowScoreCount: number;
  lowScoreThreshold?: number;
  busy?: boolean;
  onSelectLowScore: () => void;
  onSelectAllVisible: () => void;
  onClear: () => void;
  onPass: (reason: ActionReason) => void;
  onReset: () => void;
}) {
  const [reason, setReason] = useState<ActionReason>('wrong_role');
  const [confirming, setConfirming] = useState(false);
  const hasSelection = selectedCount > 0;

  return (
    <div
      data-testid="bulk-action-bar"
      className="sticky top-0 z-10 flex flex-wrap items-center gap-2 rounded-md border border-border bg-surface px-3 py-2 text-[12px]"
    >
      <button
        type="button"
        onClick={onSelectLowScore}
        disabled={busy || lowScoreCount === 0}
        className={btn}
      >
        Select ≤{lowScoreThreshold} ({lowScoreCount})
      </button>
      <button
        type="button"
        onClick={onSelectAllVisible}
        disabled={busy || visibleCount === 0}
        className={btn}
      >
        Select all visible ({visibleCount})
      </button>

      {hasSelection && (
        <>
          <span className="ml-1 font-mono text-muted-foreground">{selectedCount} selected</span>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            {confirming ? (
              <>
                <span className="font-medium">Pass {selectedCount} roles?</span>
                <button
                  type="button"
                  onClick={() => {
                    setConfirming(false);
                    onPass(reason);
                  }}
                  disabled={busy}
                  className={cn(btn, 'border-negative/40 bg-negative/10 text-negative')}
                >
                  Confirm
                </button>
                <button
                  type="button"
                  onClick={() => setConfirming(false)}
                  disabled={busy}
                  className={btn}
                >
                  Cancel
                </button>
              </>
            ) : (
              <>
                <label htmlFor="bulk-reason" className="sr-only">
                  Pass reason
                </label>
                <select
                  id="bulk-reason"
                  value={reason}
                  onChange={(e) => setReason(e.target.value as ActionReason)}
                  disabled={busy}
                  className="h-7 rounded-md border border-border bg-surface-2 px-1.5 text-[12px]"
                >
                  {BULK_REASONS.map((r) => (
                    <option key={r.value} value={r.value}>
                      {r.label}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  onClick={() => setConfirming(true)}
                  disabled={busy}
                  className={cn(btn, 'border-negative/40 text-negative hover:bg-negative/10')}
                >
                  Pass {selectedCount}
                </button>
                <button type="button" onClick={onReset} disabled={busy} className={btn}>
                  Reset {selectedCount}
                </button>
                <button type="button" onClick={onClear} disabled={busy} className={btn}>
                  Clear
                </button>
              </>
            )}
          </div>
        </>
      )}
    </div>
  );
}
