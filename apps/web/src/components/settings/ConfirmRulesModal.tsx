'use client';

import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';

/**
 * Confirmation modal for the Hard Rules save action.
 *
 * Heading: "Save N rule changes?" where N matches `changes.length`.
 * Body: list of changed fields formatted as `{label}: {from} → {to}`.
 * Buttons: Cancel (secondary) + Save changes (primary filled).
 */

export type RuleChange = {
  label: string;
  from: string;
  to: string;
};

export function ConfirmRulesModal({
  open,
  onOpenChange,
  changes,
  onSave,
  isSaving,
  error,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  changes: readonly RuleChange[];
  onSave: () => Promise<void> | void;
  isSaving: boolean;
  error: string | null;
}) {
  const count = changes.length;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="p-6">
        <DialogTitle className="text-base">
          {`Save ${count} rule change${count === 1 ? '' : 's'}?`}
        </DialogTitle>
        {changes.length === 0 ? (
          <p className="mt-3 text-[13px] text-muted-foreground">No changes.</p>
        ) : (
          <ul className="mt-4 flex list-none flex-col gap-2 p-0 text-[13px]">
            {changes.map((c) => (
              <li key={c.label} className="flex items-center justify-between gap-3">
                <span className="text-foreground/80">{c.label}</span>
                <span className="flex items-center gap-2 font-mono text-[12px]">
                  <span className="text-muted-foreground">{c.from}</span>
                  <span aria-hidden="true">→</span>
                  <span className="text-foreground">{c.to}</span>
                </span>
              </li>
            ))}
          </ul>
        )}
        {error && <p className="mt-4 text-[12px] text-negative">{error}</p>}
        <div className="mt-6 flex justify-end gap-2">
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            disabled={isSaving}
            className="inline-flex h-8 items-center rounded-md border border-border bg-surface px-3 text-sm hover:bg-accent disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => onSave()}
            disabled={isSaving || count === 0}
            className="inline-flex h-8 items-center rounded-md bg-primary px-3 text-sm text-primary-foreground hover:opacity-90 disabled:opacity-50"
          >
            {isSaving ? 'Saving…' : 'Save changes'}
          </button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
