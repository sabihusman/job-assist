'use client';

import { useRouter, useSearchParams } from 'next/navigation';
import { Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';

import { AppShell } from '@/components/chrome/AppShell';
import { EmptyState } from '@/components/shared/EmptyState';
import { CalibrationCard } from '@/components/triage/CalibrationCard';
import { DetailPanel } from '@/components/triage/DetailPanel';
import { FilterRow } from '@/components/triage/FilterRow';
import { ResumeVersionPicker } from '@/components/triage/ResumeVersionPicker';
import type { TriageCardAction } from '@/components/triage/TriageCard';
import { TriageList } from '@/components/triage/TriageList';
import { showErrorToast } from '@/lib/api/error-toast';
import { useRecordAction, useTriagePostings } from '@/lib/api/hooks';
import { useTriageKeyboard } from '@/lib/keyboard/useTriageKeyboard';
import { parseFilters } from '@/lib/triage/filters';
import { computeSubtitle } from '@/lib/triage/subtitle';
import type { TriageFilters } from '@/lib/triage/types';

/**
 * Triage page (PR #32b).
 *
 * Composition (top → bottom in the main column):
 *   FilterRow → CalibrationCard → TriageList (or empty / error / loading)
 *
 * The DetailPanel hangs off the right of the AppShell main slot
 * (visible at lg+ only).
 *
 * URL is the source of truth for filter state — the inner component
 * is wrapped in Suspense so static prerendering doesn't bail on
 * useSearchParams. The fallback must not itself touch useSearchParams.
 */
export default function TriagePage() {
  // The inner component owns AppShell so it can pass a dynamic subtitle
  // (PR #43). The Suspense fallback renders its own static-subtitle shell
  // because the inner's subtitle depends on useSearchParams + queries.
  return (
    <Suspense
      fallback={
        <AppShell title="Triage" subtitle="loading…" adornments={<KeyboardLegend />}>
          <PageFallback />
        </AppShell>
      }
    >
      <TriagePageInner />
    </Suspense>
  );
}

function TriagePageInner() {
  const searchParams = useSearchParams();
  const router = useRouter();

  // Memoize so equality-based effects don't re-fire on every render.
  const filters: TriageFilters = useMemo(() => parseFilters(searchParams), [searchParams]);

  const page1 = useTriagePostings(filters);
  const { data, isLoading, isError, error, refetch } = page1;

  // PR #70 / Bestiary 5.13: Load More pagination. Mirrors /applied,
  // /passed, /rejected. Second hook instance fetches the next page
  // (limit=100) when the operator clicks Load more. No URL persistence —
  // refresh resets to page 1.
  const [extraOffset, setExtraOffset] = useState<number | null>(null);
  // Reset extra-page state whenever the filter set changes (re-applying
  // sort or chips invalidates the previous Load More'd window). The
  // effect body only calls setState, so biome's useExhaustiveDependencies
  // misreads ``filters`` as unnecessary — but ``filters`` IS the trigger.
  // biome-ignore lint/correctness/useExhaustiveDependencies: filters is the intentional reset trigger
  useEffect(() => {
    setExtraOffset(null);
  }, [filters]);
  const extra = useTriagePostings({ ...filters, offset: extraOffset ?? 0 }, extraOffset !== null);
  // PR #43: fire a second light query to drive the dynamic subtitle's
  // applied count. limit=1 is enough — we only need ``total`` on the
  // response. The query is cached separately from the main triage list.
  const appliedQuery = useTriagePostings({
    ...filters,
    state: ['applied'],
    limit: 1,
    offset: 0,
  });
  const recordAction = useRecordAction();

  const page1Items = data?.items ?? [];
  const items =
    extraOffset !== null && extra.data ? [...page1Items, ...extra.data.items] : page1Items;
  const total = data?.total ?? 0;
  const hasMore = total > items.length;

  // PR #43: subtitle reads from the live query state. Both queries
  // start loading on first paint; render a placeholder until at least
  // the pending one resolves so the operator doesn't see a flash of
  // "0 pending · 0 applied". On error we fall back to the static
  // string that used to be hardcoded — preserves the legacy behavior
  // when the API is unreachable.
  const subtitle = computeSubtitle({
    pendingTotal: data?.total ?? null,
    appliedTotal: appliedQuery.data?.total ?? null,
    isPendingLoading: isLoading,
    isError: isError || appliedQuery.isError,
  });

  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const selectedId = selectedIndex !== null ? (items[selectedIndex]?.id ?? null) : null;

  // PR #47: lifted reason-picker state. Local-to-TriageCard state was
  // unreachable from the page-level keyboard handler, which is why the
  // ``2`` keybind was a no-op (toast only). Now ``2`` flips this state
  // and the card renders its picker via the prop.
  const [reasonPickerCardId, setReasonPickerCardId] = useState<string | null>(null);
  const handleToggleReason = useCallback((postingId: string) => {
    setReasonPickerCardId((prev) => (prev === postingId ? null : postingId));
  }, []);

  // Clear selection when the result set shrinks past the cursor — e.g.
  // after an optimistic remove of the last card on the page.
  useEffect(() => {
    if (selectedIndex !== null && selectedIndex >= items.length) {
      setSelectedIndex(items.length > 0 ? items.length - 1 : null);
    }
  }, [items.length, selectedIndex]);

  // Re-select index 0 when a fresh filter set loads the first time. We
  // don't always auto-select on data change to avoid yanking focus away
  // from a card the user is mid-action on.
  const hasData = items.length > 0;
  useEffect(() => {
    if (hasData && selectedIndex === null) setSelectedIndex(0);
  }, [hasData, selectedIndex]);

  // feat/resume-version-tracking: when the operator applies, intercept
  // to ask which tailored resume was sent before committing. The picker
  // overlay pauses the triage keyboard (see ``enabled`` below); on
  // select/skip it calls ``commitApply``.
  const [resumePickerCardId, setResumePickerCardId] = useState<string | null>(null);

  // Keyboard
  const handleAction = useCallback(
    (postingId: string, action: TriageCardAction) => {
      // Route applies through the resume picker instead of committing
      // immediately. Every apply trigger (key 3, TriageCard + DetailPanel
      // "Applied" buttons) funnels through here, so the picker catches
      // them all in one place.
      if (action.kind === 'applied') {
        setResumePickerCardId(postingId);
        return;
      }
      recordAction.mutate(
        {
          postingId,
          action_type: action.kind,
          reason: action.reason ?? null,
        },
        {
          onSuccess: () => {
            const verb =
              action.kind === 'interested'
                ? 'Interested'
                : action.kind === 'applied'
                  ? 'Applied'
                  : action.kind === 'not_interested'
                    ? 'Passed'
                    : action.kind === 'snoozed'
                      ? 'Snoozed'
                      : 'Reset';
            toast.success(`✓ ${verb}`);
          },
          onError: (err) => {
            // PR #73: the four-branch onError tower (MutationError vs
            // Error vs dev vs prod) collapsed into a single helper.
            // The behavior matrix is preserved: MutationError → detail,
            // any other Error → "{fallback} — {err.message}" in BOTH
            // dev and prod (Bestiary 5.12: prod-only invisibility hid
            // the cache-collision TypeError for weeks), null → fallback.
            // Plus the toast now auto-dismisses at 4500ms with a
            // close button (Bestiary 5.14).
            showErrorToast(err, "Couldn't update posting state — try refreshing");
          },
        },
      );
    },
    [recordAction],
  );

  // Commit an apply once the resume picker resolves (a chosen version id
  // or null = skip/untagged). Closes the picker; same toast/error path
  // as ``handleAction``.
  const commitApply = useCallback(
    (postingId: string, resumeVersionId: string | null) => {
      setResumePickerCardId(null);
      recordAction.mutate(
        { postingId, action_type: 'applied', resume_version_id: resumeVersionId },
        {
          onSuccess: () => toast.success('✓ Applied'),
          onError: (err) => showErrorToast(err, "Couldn't update posting state — try refreshing"),
        },
      );
    },
    [recordAction],
  );

  useTriageKeyboard(
    {
      onNext: () =>
        setSelectedIndex((i) => {
          if (i === null) return items.length > 0 ? 0 : null;
          return Math.min(items.length - 1, i + 1);
        }),
      onPrev: () =>
        setSelectedIndex((i) => {
          if (i === null) return items.length > 0 ? 0 : null;
          return Math.max(0, i - 1);
        }),
      onAction1: () => {
        if (selectedId) handleAction(selectedId, { kind: 'interested' });
      },
      onAction2: () => {
        // PR #47: open the reason picker for the focused card. Doesn't
        // commit anything — the operator picks 1-9 (or Esc) once the
        // picker has the keystrokes (its own listener takes over,
        // because we pause this hook via ``enabled`` below).
        if (selectedId) handleToggleReason(selectedId);
      },
      onAction3: () => {
        if (selectedId) handleAction(selectedId, { kind: 'applied' });
      },
      onAction4: () => {
        if (selectedId) handleAction(selectedId, { kind: 'snoozed' });
      },
      onEscape: () => setSelectedIndex(null),
    },
    /* enabled */ !recordAction.isPending &&
      reasonPickerCardId === null &&
      resumePickerCardId === null,
  );

  return (
    <AppShell title="Triage" subtitle={subtitle} adornments={<KeyboardLegend />}>
      <div className="flex">
        <MainColumn
          loading={isLoading}
          error={isError ? ((error as Error)?.message ?? 'Unknown error') : null}
          empty={!isLoading && !isError && items.length === 0}
          showing={items.length}
          total={total}
          companyFilterActive={!!filters.target_company_id}
          onClearCompanyFilter={() => {
            // Strip ``target_company_id`` while preserving other filter
            // params. Encoded via the URLSearchParams API so multi-value
            // params (tier, ats, state) round-trip correctly.
            const next = new URLSearchParams(searchParams.toString());
            next.delete('target_company_id');
            const qs = next.toString();
            router.replace(qs ? `/?${qs}` : '/', { scroll: false });
          }}
          onResetFilters={() => router.replace('/?state=triage', { scroll: false })}
          onRetry={() => refetch()}
        >
          <TriageList
            postings={items}
            selectedIndex={selectedIndex}
            reasonPickerCardId={reasonPickerCardId}
            onSelect={setSelectedIndex}
            onToggleReason={handleToggleReason}
            onAction={handleAction}
          />
          {hasMore && (
            <button
              type="button"
              onClick={() => setExtraOffset(items.length)}
              disabled={extra.isLoading}
              className="self-center rounded-md border border-border bg-surface px-3 py-1 text-[12px] hover:bg-accent disabled:opacity-50"
            >
              {extra.isLoading ? 'Loading…' : `Load more (${total - items.length} remaining)`}
            </button>
          )}
        </MainColumn>

        <DetailPanel
          selectedId={selectedId}
          onClose={() => setSelectedIndex(null)}
          onAction={handleAction}
        />
      </div>

      {/*
        Resume-version picker (feat/resume-version-tracking). Shown when
        any apply trigger (key 3 / Applied buttons) fires; pauses the
        triage keyboard. Fixed bottom-center so it's reachable regardless
        of which trigger opened it. Backdrop click = skip (apply
        untagged).
      */}
      {resumePickerCardId !== null && (
        <div className="fixed inset-0 z-50 flex items-end justify-center pb-10">
          {/* Backdrop — click anywhere outside the picker = skip (apply
              untagged). A button so it's keyboard-reachable; Esc also
              skips via the picker's own listener. */}
          <button
            type="button"
            aria-label="Skip resume tagging"
            className="absolute inset-0 cursor-default bg-black/20"
            onClick={() => commitApply(resumePickerCardId, null)}
          />
          <div className="relative w-full max-w-md px-4">
            <ResumeVersionPicker
              onSelect={(id) => commitApply(resumePickerCardId, id)}
              onSkip={() => commitApply(resumePickerCardId, null)}
            />
          </div>
        </div>
      )}
    </AppShell>
  );
}

function PageFallback() {
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-4 px-6 py-4">
      <div className="h-6 w-64 animate-pulse rounded bg-surface-2" />
      <div className="h-20 animate-pulse rounded-md border border-border bg-surface-2" />
      <LoadingSkeleton />
    </div>
  );
}

function KeyboardLegend() {
  return (
    <div className="hidden items-center gap-2 font-mono text-[11px] text-muted-foreground md:flex">
      <KeyHint>J</KeyHint>
      <KeyHint>K</KeyHint>
      nav
      <span aria-hidden="true">·</span>
      <KeyHint>1</KeyHint>
      <span>–</span>
      <KeyHint>4</KeyHint>
      act
      <span aria-hidden="true">·</span>
      <KeyHint>2</KeyHint>→<KeyHint>1</KeyHint>–<KeyHint>9</KeyHint>
      reason
    </div>
  );
}

function KeyHint({ children }: { children: React.ReactNode }) {
  return <kbd className="rounded border border-border bg-surface-2 px-1 py-0.5">{children}</kbd>;
}

function MainColumn({
  loading,
  error,
  empty,
  showing = 0,
  total = 0,
  companyFilterActive = false,
  onClearCompanyFilter,
  onResetFilters,
  onRetry,
  children,
}: {
  loading?: boolean;
  error?: string | null;
  empty?: boolean;
  showing?: number;
  total?: number;
  companyFilterActive?: boolean;
  onClearCompanyFilter?: () => void;
  onResetFilters?: () => void;
  onRetry?: () => void;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-4 px-6 py-4">
      {companyFilterActive && (
        // PR #71: scoped-to-one-company indicator. Clicking the × strips
        // ``target_company_id`` from the URL and lands back on the full
        // Triage queue. Generic copy (no company-name lookup) keeps the
        // scope tight — operator knows what they clicked.
        <div className="flex items-center gap-2">
          <span
            data-testid="company-filter-pill"
            className="inline-flex h-6 items-center gap-2 rounded-full border border-border bg-accent/40 px-2 font-mono text-[11px] text-foreground"
          >
            Filtered to one company
            <button
              type="button"
              aria-label="Clear company filter"
              onClick={onClearCompanyFilter}
              className="inline-flex h-4 w-4 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground"
            >
              ×
            </button>
          </span>
        </div>
      )}
      <FilterRow showing={showing} total={total} />
      <CalibrationCard />

      {error ? (
        <section className="rounded-md border border-negative/40 bg-negative/5 p-4">
          <h2 className="text-sm font-semibold text-negative">Couldn&apos;t load postings.</h2>
          <p className="mt-1 text-[13px] text-muted-foreground">{error}</p>
          <button
            type="button"
            onClick={onRetry}
            className="mt-3 inline-flex h-8 items-center rounded-md border border-border bg-surface px-3 text-sm hover:bg-accent"
          >
            Retry
          </button>
        </section>
      ) : loading ? (
        <LoadingSkeleton />
      ) : empty ? (
        // PR 2: migrated from a local 11-line ad-hoc EmptyState to the
        // PR #77 shared primitive. Same testId so existing E2E and
        // unit assertions still find it.
        <EmptyState
          testId="empty-state"
          title="No postings match your filters."
          description="Try removing some filters or come back tomorrow."
          action={
            onResetFilters && (
              <button
                type="button"
                onClick={onResetFilters}
                className="inline-flex h-8 items-center rounded-md border border-border bg-surface px-3 text-sm hover:bg-accent"
              >
                Reset filters
              </button>
            )
          }
        />
      ) : (
        children
      )}
    </div>
  );
}

function LoadingSkeleton() {
  return (
    <div className="flex flex-col gap-3">
      {[0, 1, 2, 3, 4].map((i) => (
        <div
          key={i}
          className="h-[88px] animate-pulse rounded-md border border-border bg-surface-2"
        />
      ))}
    </div>
  );
}
