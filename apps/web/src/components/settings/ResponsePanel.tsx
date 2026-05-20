'use client';

import { X } from 'lucide-react';

/**
 * Pretty-printed JSON response panel for the Manual Job rows.
 * Self-contained — takes the response payload + an `onDismiss`
 * callback. The parent decides when to show/hide.
 */
export function ResponsePanel({
  response,
  onDismiss,
}: {
  response: unknown;
  onDismiss: () => void;
}) {
  return (
    <div className="relative mt-2 rounded-md border border-border bg-surface-2 p-3">
      <button
        type="button"
        onClick={onDismiss}
        aria-label="Dismiss response"
        className="absolute right-2 top-2 inline-flex h-6 w-6 items-center justify-center rounded text-muted-foreground hover:text-foreground"
      >
        <X className="h-3.5 w-3.5" />
      </button>
      <h4 className="mb-2 font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
        Response
      </h4>
      <pre className="overflow-x-auto whitespace-pre-wrap break-words font-mono text-[12px] text-foreground/90">
        {JSON.stringify(response, null, 2)}
      </pre>
    </div>
  );
}
