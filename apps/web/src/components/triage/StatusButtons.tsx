'use client';

import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { toast } from 'sonner';

import { API_BASE_URL } from '@/lib/api/client';
import { showErrorToast } from '@/lib/api/error-toast';
import { queryKeys } from '@/lib/api/hooks';
import type { ResolvedStatus } from '@/lib/triage/types';
import { cn } from '@/lib/utils';

/**
 * Manual application-status buttons (feat/manual-application-status Phase 1).
 *
 * Five lifecycle stages — Applied · Interview · Offer · Accepted · Rejected —
 * each firing PUT /postings/{id}/status through the same-origin /api/be proxy
 * (token injected server-side), mirroring the ResumeAttach fetch pattern.
 *
 * The backend recomputes resolved_status, so a terminal status
 * (Accepted/Rejected) drops the card out of the Applied tab and Rejected lands
 * it in the Rejected tab. We invalidate the detail query + every postings list
 * so the tabs re-filter immediately.
 *
 * The Gmail-rejection hint is INFORMATIONAL: it never moves the card — the
 * status button is authoritative.
 */

const STAGES: readonly { value: ResolvedStatus; label: string }[] = [
  { value: 'applied', label: 'Applied' },
  { value: 'interview', label: 'Interview' },
  { value: 'offer', label: 'Offer' },
  { value: 'accepted', label: 'Accepted' },
  { value: 'rejected', label: 'Rejected' },
];

export function StatusButtons({
  postingId,
  current,
  companyName,
  gmailRejectionHint = false,
}: {
  postingId: string;
  current: ResolvedStatus | null;
  companyName?: string | null;
  gmailRejectionHint?: boolean;
}) {
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);

  async function setStatus(status: ResolvedStatus) {
    setBusy(true);
    try {
      const res = await fetch(`${API_BASE_URL}/postings/${postingId}/status`, {
        method: 'PUT',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ status }),
      });
      if (!res.ok) throw new Error(`Status update failed (${res.status})`);
      toast.success(`✓ Marked ${status}`);
      // Detail + every list query: the Applied/Rejected tabs re-filter on
      // resolved_status, so a terminal status drops the card out of Applied.
      await Promise.all([
        qc.invalidateQueries({ queryKey: queryKeys.posting(postingId) }),
        qc.invalidateQueries({ queryKey: ['postings'] }),
      ]);
    } catch (err) {
      showErrorToast(err, "Couldn't update status");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      className="mt-6 rounded-md border border-border bg-surface p-3"
      aria-label="Application status"
    >
      <h3 className="text-[13px] font-semibold">Application status</h3>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {STAGES.map((s) => {
          const active = current === s.value;
          return (
            <button
              key={s.value}
              type="button"
              aria-pressed={active}
              disabled={busy}
              onClick={() => void setStatus(s.value)}
              className={cn(
                'inline-flex h-7 items-center rounded-md border px-3 text-[12px] disabled:opacity-50',
                active
                  ? 'border-primary bg-primary/15 text-primary'
                  : 'border-border bg-surface-2 hover:bg-accent',
              )}
            >
              {s.label}
            </button>
          );
        })}
      </div>
      {gmailRejectionHint && (
        <p className="mt-2 text-[11px] text-muted-foreground">
          Gmail saw a rejection{companyName ? ` from ${companyName}` : ''}. Informational only —
          your status button is authoritative.
        </p>
      )}
    </section>
  );
}
