'use client';

import { useRouter, useSearchParams } from 'next/navigation';
import { Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';

import { AppShell } from '@/components/chrome/AppShell';
import { CalibrationCard } from '@/components/triage/CalibrationCard';
import { DetailPanel } from '@/components/triage/DetailPanel';
import { FilterRow } from '@/components/triage/FilterRow';
import type { TriageCardAction } from '@/components/triage/TriageCard';
import { TriageList } from '@/components/triage/TriageList';
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

  const { data, isLoading, isError, error, refetch } = useTriagePostings(filters);
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

  const items = data?.items ?? [];
  const total = data?.total ?? 0;

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

  // Keyboard
  const handleAction = useCallback(
    (postingId: string, action: TriageCardAction) => {
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
          onError: () => {
            toast.error('Action failed — try again');
          },
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
    /* enabled */ !recordAction.isPending && reasonPickerCardId === null,
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
        </MainColumn>

        <DetailPanel
          selectedId={selectedId}
          onClose={() => setSelectedIndex(null)}
          onAction={handleAction}
        />
      </div>
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
  onResetFilters,
  onRetry,
  children,
}: {
  loading?: boolean;
  error?: string | null;
  empty?: boolean;
  showing?: number;
  total?: number;
  onResetFilters?: () => void;
  onRetry?: () => void;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-4 px-6 py-4">
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
        <EmptyState onReset={onResetFilters} />
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

function EmptyState({ onReset }: { onReset?: () => void }) {
  return (
    <section
      data-testid="empty-state"
      className="flex flex-col items-center gap-3 rounded-md border border-border bg-card px-6 py-12 text-center"
    >
      <h2 className="text-sm font-semibold">No postings match your filters.</h2>
      <p className="text-[13px] text-muted-foreground">
        Try removing some filters or come back tomorrow.
      </p>
      {onReset && (
        <button
          type="button"
          onClick={onReset}
          className="inline-flex h-8 items-center rounded-md border border-border bg-surface px-3 text-sm hover:bg-accent"
        >
          Reset filters
        </button>
      )}
    </section>
  );
}
