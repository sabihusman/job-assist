'use client';

import { useEffect } from 'react';

import type { ActionReason } from '@/lib/triage/types';
import { cn } from '@/lib/utils';

/**
 * Inline 9-chip reason picker that expands beneath a TriageCard's meta
 * row when the operator presses `2`/clicks Pass, and also lives in the
 * DetailPanel action bar.
 *
 * Hotkeys (1-9) commit a reason; Esc cancels. Listeners attach as long
 * as the picker is mounted — the consuming card unmounts the picker
 * when collapsed, so this hook is naturally scoped.
 *
 * PR #43 added chips 8 (too_senior) and 9 (too_junior) so the
 * calibration card can split "comp" mismatches from "level" mismatches.
 *
 * Inputs trigger no auto-submit (numbers typed inside an input field
 * are user data, not shortcuts) — same focus-guard pattern as
 * `useTriageKeyboard`.
 */

type Choice = {
  reason: ActionReason;
  label: string;
  hotkey: '1' | '2' | '3' | '4' | '5' | '6' | '7' | '8' | '9' | '0';
};

export const REASON_CHOICES: readonly Choice[] = [
  { reason: 'wrong_role', label: 'Wrong role', hotkey: '1' },
  { reason: 'wrong_location', label: 'Wrong location', hotkey: '2' },
  { reason: 'comp_too_low', label: 'Comp too low', hotkey: '3' },
  { reason: 'wrong_industry', label: 'Wrong industry', hotkey: '4' },
  { reason: 'wrong_stage', label: 'Wrong stage', hotkey: '5' },
  { reason: 'already_rejected_here', label: 'Already rejected here', hotkey: '6' },
  { reason: 'just_not_feeling_it', label: 'Just not feeling it', hotkey: '7' },
  { reason: 'too_senior', label: 'Too senior', hotkey: '8' },
  { reason: 'too_junior', label: 'Too junior', hotkey: '9' },
  // feat/company-app-awareness: a reluctant portfolio pass (not a fit signal).
  // Hotkey 0 — the last single digit left after PR #43 took 8/9.
  { reason: 'too_many_open_apps', label: 'Too many open apps here', hotkey: '0' },
];

function isEditable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable;
}

export function ReasonPicker({
  onSelect,
  onCancel,
  className,
}: {
  onSelect: (reason: ActionReason) => void;
  onCancel: () => void;
  className?: string;
}) {
  useEffect(() => {
    const listener = (e: KeyboardEvent) => {
      if (isEditable(e.target)) return;
      if (e.key === 'Escape') {
        e.preventDefault();
        onCancel();
        return;
      }
      const choice = REASON_CHOICES.find((c) => c.hotkey === e.key);
      if (choice) {
        e.preventDefault();
        onSelect(choice.reason);
      }
    };
    window.addEventListener('keydown', listener);
    return () => window.removeEventListener('keydown', listener);
  }, [onSelect, onCancel]);

  return (
    <fieldset className={cn('flex flex-col gap-2', className)} aria-label="Reason">
      <div className="flex items-center justify-between">
        <span className="font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
          Why not?
        </span>
        <button
          type="button"
          onClick={onCancel}
          aria-label="Cancel reason · esc"
          title="Cancel · esc"
          className="inline-flex h-6 items-center gap-1 rounded px-1.5 text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
        >
          <span aria-hidden="true">×</span>
          <span className="font-mono text-[10px]">esc</span>
        </button>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {REASON_CHOICES.map((c) => (
          <button
            key={c.reason}
            type="button"
            onClick={() => onSelect(c.reason)}
            className="inline-flex h-7 items-center gap-1.5 rounded border border-border bg-surface px-2 text-xs text-foreground/80 hover:bg-accent"
          >
            <span>{c.label}</span>
            <span className="font-mono text-[10px] text-muted-foreground">{c.hotkey}</span>
          </button>
        ))}
      </div>
    </fieldset>
  );
}
