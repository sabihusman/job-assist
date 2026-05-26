'use client';

/**
 * Contact detail panel (PR #52).
 *
 * Responsive panel pattern — established in this PR; future panels
 * (PR #53 outreach detail, etc.) should match. Conventions:
 *
 *   - Position: ``inset-x-0 bottom-0 lg:inset-y-0 lg:right-0``.
 *     On small viewports the panel docks to the bottom; at ``lg+``
 *     it slides in from the right. The Triage DetailPanel from
 *     PR #32b is right-only because it pre-dates this convention;
 *     it'll align in a future polish PR.
 *   - Slide-in: ``translate-y-full`` (small) / ``lg:translate-x-full``
 *     (large) when closed; ``translate-{y,x}-0`` when open.
 *   - Max height: ``max-h-[85vh]`` on small so the page underneath
 *     stays partially visible.
 *   - Dismiss: explicit close button only. Swipe-down + Esc-to-close
 *     are YAGNI for v1.
 *   - Width: ``lg:w-[28rem]`` (448px) — wide enough for two-column
 *     editable fields without dominating the page.
 *
 * Open/close is controlled by the parent (the Contacts page); this
 * component is purely presentational + dispatches mutations.
 */

import { X } from 'lucide-react';
import { useEffect } from 'react';
import { toast } from 'sonner';

import { ContactEditForm } from '@/components/contacts/ContactEditForm';
import { CONTACT_SOURCE_LABELS } from '@/components/contacts/ContactsTable';
import { LogOutreachForm } from '@/components/contacts/LogOutreachForm';
import { OutreachTimeline } from '@/components/contacts/OutreachTimeline';
import {
  useContactArchive,
  useContactDetail,
  useContactOutreach,
  useContactUnarchive,
} from '@/lib/api/contacts';
import type { ContactSourceType } from '@/lib/contacts/types';
import { cn } from '@/lib/utils';

export type ContactDetailPanelProps = {
  /** ``null`` when the panel is closed. */
  contactId: string | null;
  onClose: () => void;
};

export function ContactDetailPanel({ contactId, onClose }: ContactDetailPanelProps) {
  const open = contactId !== null;
  const { data, isLoading, isError, error } = useContactDetail(contactId);
  const outreachQuery = useContactOutreach(contactId);
  const archive = useContactArchive();
  const unarchive = useContactUnarchive();

  const isArchived = data?.archived_at !== null && data?.archived_at !== undefined;

  // Reset mutation states whenever the operator opens a DIFFERENT
  // contact. The deps are intentionally just ``contactId`` — adding
  // ``archive`` / ``unarchive`` to deps would re-fire on every render
  // (mutation objects are not stable), which would clobber the
  // post-mutate UI state. The reset is a "transition" effect, keyed
  // on contactId only.
  // biome-ignore lint/correctness/useExhaustiveDependencies: intentional contactId-only deps
  useEffect(() => {
    archive.reset();
    unarchive.reset();
  }, [contactId]);

  const handleArchiveToggle = () => {
    if (!contactId) return;
    const mut = isArchived ? unarchive : archive;
    mut.mutate(contactId, {
      onSuccess: () => {
        toast.success(`✓ ${isArchived ? 'Unarchived' : 'Archived'}`);
      },
      onError: (err) => {
        const isMutationError =
          err && typeof err === 'object' && 'detail' in err && 'status' in err;
        const detail = isMutationError
          ? (err as unknown as { detail: string | null }).detail
          : null;
        toast.error(detail ?? `${isArchived ? 'Unarchive' : 'Archive'} failed.`);
      },
    });
  };

  return (
    <aside
      data-testid="contact-detail-panel"
      data-open={open}
      aria-hidden={!open}
      aria-label="Contact detail"
      className={cn(
        // Position — bottom-docked on small, right-slide on large.
        'fixed inset-x-0 bottom-0 z-40 flex flex-col border-border bg-card shadow-xl transition-transform duration-200 ease-out',
        'border-t lg:inset-y-0 lg:right-0 lg:left-auto lg:w-[28rem] lg:border-l lg:border-t-0',
        // Max-height keeps the page underneath visible on small viewports.
        'max-h-[85vh] lg:max-h-none',
        // Slide direction differs per viewport (translate-y vs translate-x).
        open
          ? 'translate-y-0 lg:translate-x-0'
          : 'translate-y-full lg:translate-y-0 lg:translate-x-full',
      )}
    >
      {/* Header */}
      <header className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
        <div className="min-w-0">
          {data ? (
            <>
              <h2 className="truncate text-sm font-semibold">
                {data.preferred_first_name && data.preferred_first_name !== data.first_name
                  ? `${data.first_name} (${data.preferred_first_name}) ${data.last_name}`
                  : `${data.first_name} ${data.last_name}`}
              </h2>
              <p className="mt-0.5 truncate text-[12px] text-muted-foreground">
                {(CONTACT_SOURCE_LABELS as Record<string, string>)[
                  data.source_type as ContactSourceType
                ] ?? data.source_type}
                {isArchived && (
                  <span
                    data-testid="archived-badge"
                    className="ml-2 rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground"
                  >
                    Archived
                  </span>
                )}
              </p>
            </>
          ) : (
            <h2 className="text-sm font-semibold text-muted-foreground">…</h2>
          )}
        </div>
        <div className="flex items-center gap-2">
          {data && (
            <button
              type="button"
              onClick={handleArchiveToggle}
              disabled={archive.isPending || unarchive.isPending}
              className="rounded-md border border-border bg-surface px-2 py-1 text-[12px] hover:bg-accent disabled:opacity-50"
            >
              {isArchived ? 'Unarchive' : 'Archive'}
            </button>
          )}
          <button
            type="button"
            onClick={onClose}
            aria-label="Close detail panel"
            className="rounded-md border border-border bg-surface p-1 hover:bg-accent"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
      </header>

      {/* Scroll region. The panel is always mounted (closed via
          ``translate-y-full`` for the slide animation), so we must
          NOT render the loading skeleton when the panel is closed —
          its three ``animate-pulse`` elements would otherwise be
          present in the DOM whenever ``contactId === null`` and the
          page-level E2E ``waitForDataReady`` would block forever
          waiting for them to disappear. Gate the skeleton on ``open``
          so the closed panel renders nothing in this region. */}
      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-4 py-4">
        {!open ? null : isError ? (
          <p className="text-[13px] text-negative">
            {(error as Error)?.message ?? 'Failed to load contact.'}
          </p>
        ) : isLoading || !data ? (
          <div className="space-y-2">
            <div className="h-4 w-3/4 animate-pulse rounded bg-surface-2" />
            <div className="h-4 w-1/2 animate-pulse rounded bg-surface-2" />
            <div className="h-4 w-2/3 animate-pulse rounded bg-surface-2" />
          </div>
        ) : (
          <>
            <ContactEditForm contact={data} />

            <section className="flex flex-col gap-2">
              <h3 className="font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
                Outreach
              </h3>
              <LogOutreachForm contactId={data.id} />
              <OutreachTimeline
                contactId={data.id}
                items={outreachQuery.data?.items ?? []}
                total={outreachQuery.data?.total ?? 0}
                isLoading={outreachQuery.isLoading}
              />
            </section>
          </>
        )}
      </div>
    </aside>
  );
}
