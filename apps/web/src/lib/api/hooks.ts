'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/lib/api/client';
import { MutationError, extractDetail } from '@/lib/api/mutation-error';
import type {
  ActionReason,
  ActionType,
  CalibrationResponse,
  PostingDetail,
  PostingsListResponse,
  TriageFilters,
} from '@/lib/triage/types';

/**
 * Thin react-query wrappers per public endpoint. Pages reach for these
 * rather than calling `api.GET(...)` directly so the query keys stay
 * consistent across cache lookups.
 *
 * Wire types are defined in `lib/triage/types.ts` rather than the
 * generated `openapi.ts` because the FastAPI endpoints return generic
 * `dict[str, Any]` — `openapi-typescript` widens those to
 * `Record<string, never>`, which would mean every consumer reaches
 * through `as unknown as ...`. The narrower types live next to the
 * domain logic that uses them.
 */

export const queryKeys = {
  postings: (params: Record<string, unknown> = {}) => ['postings', params] as const,
  /**
   * Saved-filter count badges. Bestiary 5.12: must NOT share the
   * ``['postings', ...]`` prefix because the cached value is a bare
   * ``number`` (just ``.total``) while the list hooks store full
   * ``PostingsListResponse`` objects. ``useRecordAction.onMutate``
   * iterates ``['postings', ...]`` to optimistically drop the acted-on
   * row; if it finds a numeric entry under the same prefix it crashes
   * on ``prev.items.filter`` and the mutation never reaches the wire.
   */
  postingsCount: (params: Record<string, unknown> = {}) => ['postings-count', params] as const,
  posting: (id: string) => ['posting', id] as const,
  calibration: () => ['calibration'] as const,
};

// ── /postings ───────────────────────────────────────────────────────────

/** Serialise filters into the openapi-fetch query object. */
function toQuery(filters: TriageFilters): Record<string, unknown> {
  const q: Record<string, unknown> = {
    limit: filters.limit,
    offset: filters.offset,
  };
  if (filters.tier.length) q.tier = filters.tier;
  if (filters.ats.length) q.ats = filters.ats;
  if (filters.remote_type.length) q.remote_type = filters.remote_type;
  if (filters.role_family.length) q.role_family = filters.role_family;
  if (filters.state.length) q.state = filters.state;
  if (filters.include_snoozed_past_only) q.include_snoozed_past_only = true;
  if (filters.target_company_id) q.target_company_id = filters.target_company_id;
  // PR #49: only send ?sort= when not the default. Keeps the cache key
  // identical between "default selected" and "no sort param" — both
  // resolve to the same backend ORDER BY.
  if (filters.sort && filters.sort !== 'newest') q.sort = filters.sort;
  return q;
}

/**
 * Paginated triage list. Key includes the serialised filters so that
 * changing chips invalidates correctly without manual cache busts.
 */
export function useTriagePostings(filters: TriageFilters, enabled = true) {
  const query = toQuery(filters);
  return useQuery({
    queryKey: queryKeys.postings(query),
    queryFn: async () => {
      // The openapi-fetch query type is too narrow for arrays — cast
      // through unknown. The runtime wire format is correct.
      const { data, error } = await api.GET('/postings', {
        params: { query: query as never },
      });
      if (error) throw error;
      return data as unknown as PostingsListResponse;
    },
    enabled,
    refetchOnWindowFocus: false,
    staleTime: 30_000,
  });
}

/** Single posting detail with division + state_history. */
export function usePosting(id: string | null) {
  return useQuery({
    queryKey: id ? queryKeys.posting(id) : ['posting', '__none__'],
    queryFn: async () => {
      if (!id) throw new Error('usePosting called without id');
      const { data, error } = await api.GET('/postings/{posting_id}', {
        params: { path: { posting_id: id } },
      });
      if (error) throw error;
      return data as unknown as PostingDetail;
    },
    enabled: !!id,
    refetchOnWindowFocus: false,
    staleTime: 30_000,
  });
}

// ── /stats/calibration ──────────────────────────────────────────────────

export function useCalibration() {
  return useQuery({
    queryKey: queryKeys.calibration(),
    queryFn: async () => {
      const { data, error } = await api.GET('/stats/calibration', {});
      if (error) throw error;
      return data as unknown as CalibrationResponse;
    },
    refetchOnWindowFocus: false,
    staleTime: 5 * 60 * 1000,
  });
}

// ── POST /postings/{id}/state with optimistic update ────────────────────

export type RecordActionVars = {
  postingId: string;
  action_type: ActionType;
  reason?: ActionReason | null;
  snooze_until?: string | null;
  notes?: string | null;
};

/**
 * Wire-body serializer for ``POST /postings/{id}/state``.
 *
 * **PR #58 root cause**: a production probe confirmed the deployed
 * frontend was POSTing ``{kind, reason}``; the FastAPI ``ActionCreate``
 * schema expects ``{action_type, reason}``. The 422 silently rolled
 * back the optimistic UI to "phantom success" — historical pass /
 * apply / reject actions never persisted server-side.
 *
 * This serializer exists so the wire shape is no longer implicit. It
 * accepts either the canonical ``RecordActionVars`` (``action_type``)
 * or a defensive ``{kind, ...}`` shape (the bug shape) and always
 * emits the API contract. Consumers reach for ``RecordActionVars``;
 * the ``kind`` branch is a belt-and-braces guard against the same
 * footgun re-appearing in a future refactor.
 *
 * The unit test ``hooks.test.tsx > 'POST body always carries
 * action_type'`` locks this contract — if the wire payload ever
 * regresses to ``{kind, ...}`` again, that test fails before the bug
 * reaches production.
 */
export function toStateRequestBody(
  vars: RecordActionVars | { kind: ActionType; reason?: ActionReason | null },
): {
  action_type: ActionType;
  reason: ActionReason | null;
  snooze_until: string | null;
  notes: string | null;
} {
  const action_type =
    'action_type' in vars && vars.action_type
      ? vars.action_type
      : (vars as { kind: ActionType }).kind;
  const reason = (vars as { reason?: ActionReason | null }).reason ?? null;
  const snooze_until = (vars as RecordActionVars).snooze_until ?? null;
  const notes = (vars as RecordActionVars).notes ?? null;
  return { action_type, reason, snooze_until, notes };
}

/**
 * Record an operator action. Optimistically removes the posting from
 * any cached triage list (cards that just transitioned out of triage
 * should disappear instantly). On error, restores the snapshot. On
 * settle, invalidates calibration so the KPI card refreshes.
 *
 * Throws a ``MutationError`` carrying the structured ``detail`` from
 * the API body so the page-level onError handler can surface it in
 * the toast verbatim. No 5xx retry — the diagnosed Vanta failure was
 * an application bug (wrong field name), not a transient cold-start.
 */
export function useRecordAction() {
  const qc = useQueryClient();

  return useMutation({
    mutationFn: async (vars: RecordActionVars) => {
      const { data, error, response } = await api.POST('/postings/{posting_id}/state', {
        params: { path: { posting_id: vars.postingId } },
        body: toStateRequestBody(vars),
      });
      if (error || data === undefined) {
        throw new MutationError({
          kind: 'application',
          status: response?.status ?? null,
          detail: extractDetail(error),
          message: extractDetail(error) ?? `Action failed (${response?.status ?? 'no status'})`,
        });
      }
      return data;
    },
    onMutate: async (vars) => {
      // Pause any in-flight list refetches so they don't clobber our
      // optimistic state mid-flight.
      await qc.cancelQueries({ queryKey: ['postings'] });

      // Snapshot every cached `['postings', ...]` query so we can
      // roll back if the POST fails.
      const snapshots = qc.getQueriesData<PostingsListResponse>({
        queryKey: ['postings'],
      });

      for (const [key, prev] of snapshots) {
        if (!prev) continue;
        // Bestiary 5.12: defense-in-depth shape guard. The primary fix
        // is that ``useSavedFilterCount`` no longer shares the
        // ``['postings', ...]`` cache key, so this loop only sees
        // ``PostingsListResponse`` entries. But if a future hook ever
        // lands under this prefix with a different shape, we skip it
        // here instead of crashing on ``prev.items.filter``.
        if (typeof prev !== 'object' || !('items' in prev) || !Array.isArray(prev.items)) continue;
        // Drop the acted-on posting from the cached list and bump the
        // total down by one. We don't try to be clever about which
        // filters this affects — list views consume their own filter,
        // and the user has just made a decision that moves the card
        // out of whatever bucket they were looking at.
        const next: PostingsListResponse = {
          ...prev,
          total: Math.max(0, prev.total - 1),
          items: prev.items.filter((p) => p.id !== vars.postingId),
        };
        qc.setQueryData(key, next);
      }

      return { snapshots };
    },
    onError: (_err, _vars, ctx) => {
      // Restore every snapshot we took. Failed POSTs are rare and
      // they should never leave the UI in a phantom-removed state.
      if (!ctx?.snapshots) return;
      for (const [key, prev] of ctx.snapshots) {
        qc.setQueryData(key, prev);
      }
    },
    onSettled: () => {
      // Refetch fresh data from the server. Drives the list views, the
      // saved-filter count badges in the Sidebar (separate key prefix
      // since PR #68 / Bestiary 5.12), and the calibration card.
      qc.invalidateQueries({ queryKey: ['postings'] });
      qc.invalidateQueries({ queryKey: ['postings-count'] });
      qc.invalidateQueries({ queryKey: queryKeys.calibration() });
    },
  });
}

// ── Saved-filter count badges ───────────────────────────────────────────

/**
 * Fetch JUST the `.total` for a filter set. Used in the Sidebar
 * SavedFilters rows to show real count badges. `limit=1` keeps the
 * payload tiny since we throw away `.items`.
 */
export function useSavedFilterCount(filterParams: Record<string, unknown>) {
  const query: Record<string, unknown> = { ...filterParams, limit: 1, offset: 0 };
  return useQuery({
    queryKey: queryKeys.postingsCount(query),
    queryFn: async () => {
      const { data, error } = await api.GET('/postings', {
        params: { query: query as never },
      });
      if (error) throw error;
      return (data as unknown as PostingsListResponse).total;
    },
    refetchOnWindowFocus: false,
    staleTime: 5 * 60 * 1000,
  });
}

// ── Legacy smoke fetch (PR #32a leftover, kept for the home page logger) ──

export function usePostings() {
  return useQuery({
    queryKey: queryKeys.postings({ limit: 20, offset: 0 }),
    queryFn: async () => {
      const { data, error } = await api.GET('/postings', {
        params: { query: { limit: 20, offset: 0 } },
      });
      if (error) throw error;
      return data;
    },
    refetchOnWindowFocus: false,
    staleTime: 30_000,
  });
}
