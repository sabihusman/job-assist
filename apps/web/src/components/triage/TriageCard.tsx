'use client';

import { Clock, MapPin } from 'lucide-react';

import { ActionButton } from '@/components/shared/ActionButton';
import { CompanyAvatar } from '@/components/shared/CompanyAvatar';
import { FitScoreBadge } from '@/components/triage/FitScoreBadge';
import { ReasonPicker } from '@/components/triage/ReasonPicker';
import type { ActionReason, ActionType, PostingListItem } from '@/lib/triage/types';
import { cn } from '@/lib/utils';

/**
 * One repeating list card. Every visual detail is anchored to
 * UI_SPEC.md "Triage card (primary repeating component)".
 *
 * State (all lifted, PR #47):
 *  - `isSelected` — drives the selected styling and the right
 *    detail panel.
 *  - `reasonOpen` — true when the operator pressed Pass (button or
 *    keyboard `2`) and the ReasonPicker should expand beneath the
 *    meta row. Lifted because the page-level keyboard handler needs
 *    to flip it on the `2` chord — local state on the card was
 *    unreachable from the parent's keydown listener (the bug PR #47
 *    fixes).
 *  - `onToggleReason` — flips ``reasonOpen``. Called from the Pass
 *    button click, the ReasonPicker's cancel (Esc/×), and after a
 *    successful reason commit.
 */

export type TriageCardAction = { kind: ActionType; reason?: ActionReason };

export function TriageCard({
  posting,
  isSelected,
  reasonOpen,
  isChecked = false,
  onSelect,
  onToggleReason,
  onAction,
  onToggleCheck,
}: {
  posting: PostingListItem;
  isSelected: boolean;
  reasonOpen: boolean;
  // feat/bulk-triage-actions: multi-select checkbox state. The checkbox only
  // renders when ``onToggleCheck`` is supplied (the triage page wires it; other
  // surfaces that reuse the card don't).
  isChecked?: boolean;
  onSelect: () => void;
  onToggleReason: () => void;
  onAction: (action: TriageCardAction) => void;
  onToggleCheck?: () => void;
}) {
  const handlePassAction = () => {
    onToggleReason();
  };

  const handlePickReason = (reason: ActionReason) => {
    // Close the picker first so a re-render of the same card after
    // the action lands doesn't leave a stale picker visible.
    onToggleReason();
    onAction({ kind: 'not_interested', reason });
  };

  const company = posting.company;
  const role = posting.role;
  const tier = company.tier ?? 4;
  const tierColorClass = tierStripClass(tier);
  const remote = posting.remote_type ?? null;

  return (
    <article
      data-selected={isSelected}
      className={cn(
        'group relative flex gap-3 rounded-md border bg-card px-4 py-3 shadow-card transition-colors',
        isSelected
          ? 'border-border-strong bg-accent/40'
          : 'border-border hover:border-border-strong hover:bg-accent/30',
      )}
    >
      {/* Tier strip — selected card overrides to primary teal. */}
      <span
        aria-hidden="true"
        data-testid="tier-strip"
        className={cn(
          'absolute left-0 top-3 h-[calc(100%-1.5rem)] w-0.5 rounded-r',
          isSelected ? 'bg-primary' : tierColorClass,
        )}
      />

      {/* feat/bulk-triage-actions: per-card multi-select checkbox. stopPropagation
          keeps a checkbox click from also triggering the card-select button. */}
      {onToggleCheck && (
        <input
          type="checkbox"
          checked={isChecked}
          aria-label={`Select ${company.name} — ${role.title}`}
          onChange={onToggleCheck}
          onClick={(e) => e.stopPropagation()}
          className="mt-1.5 h-4 w-4 shrink-0 cursor-pointer accent-primary"
        />
      )}

      {/* Card body — clicking selects, but the action column is excluded
          via stopPropagation in its own buttons. */}
      <button
        type="button"
        onClick={onSelect}
        className="flex min-w-0 flex-1 items-start gap-3 text-left"
        aria-label={`Open detail for ${company.name} — ${role.title}`}
      >
        <CompanyAvatar name={company.name} size={32} />

        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          {/* PR 2: Row 1 reshuffled — company + identity badges on the
              left, score badge + status pill pushed to the right. Score
              on Row 1 was the primary density change from the audit
              (was on the meta row, lost among location/salary). The
              ``min-w-0`` on the left cluster + ``shrink-0`` on the
              right cluster guarantees the score never gets clipped
              even at very narrow viewports. */}
          <div className="flex items-center gap-2">
            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
              <span className="truncate text-md font-semibold">{company.name}</span>
              <TierBadge tier={tier} />
              <AtsBadge ats={posting.source.ats} />
              <span className="inline-flex items-center gap-1 font-mono text-xs text-muted-foreground">
                <Clock className="h-3 w-3" aria-hidden="true" />
                {timeAgo(posting.first_seen_at)}
              </span>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <FitScoreBadge score={posting.score} />
              {posting.state.current && <StatusPill state={posting.state.current} />}
            </div>
          </div>

          {/* Row 2 — company tagline, falls back to description excerpt. */}
          {company.description && (
            <span className="truncate text-xs text-muted-foreground">{company.description}</span>
          )}

          {/* Row 3 — role title (truncated; full title surfaces on hover
              via the title= attribute so operators can disambiguate
              without opening the detail panel). */}
          <span className="truncate text-base font-semibold" title={role.title}>
            {role.title}
          </span>

          {/* Row 4 — meta (Score moved up to Row 1, so this row is
              now Location · Salary · Remote only). */}
          <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 text-sm text-muted-foreground">
            {posting.location_raw && (
              <span className="inline-flex min-w-0 items-center gap-1">
                <MapPin className="h-3 w-3 shrink-0" aria-hidden="true" />
                <span className="truncate">{posting.location_raw}</span>
              </span>
            )}
            {posting.location_raw && posting.salary && (
              <span aria-hidden="true" className="shrink-0">
                ·
              </span>
            )}
            {posting.salary && <SalaryChip salary={posting.salary} />}
            {remote && <RemoteBadge remote={String(remote)} />}
          </div>
        </div>
      </button>

      {/* Action column */}
      <div
        className="flex items-start gap-1.5"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => e.stopPropagation()}
        role="toolbar"
        aria-label="Actions"
      >
        <ActionButton
          variant="interested"
          size="compact"
          onClick={() => onAction({ kind: 'interested' })}
        />
        <ActionButton variant="pass" size="compact" onClick={handlePassAction} />
        <ActionButton
          variant="applied"
          size="compact"
          onClick={() => onAction({ kind: 'applied' })}
        />
        <ActionButton
          variant="snooze"
          size="compact"
          onClick={() => onAction({ kind: 'snoozed' })}
        />
      </div>

      {/* Inline reason picker — only rendered when expanded so its
          keyboard listener doesn't compete with the page-level one
          (the page-level hook is also paused via ``enabled=false``
          when ``reasonOpen`` is true; this is belt-and-suspenders). */}
      {reasonOpen && (
        <div className="absolute inset-x-4 bottom-2 top-auto translate-y-full rounded-md border border-border bg-surface-2 p-3">
          <ReasonPicker onSelect={handlePickReason} onCancel={onToggleReason} />
        </div>
      )}
    </article>
  );
}

// ── Sub-presentation pieces ─────────────────────────────────────────────

function TierBadge({ tier }: { tier: number }) {
  const colorClass =
    (
      {
        1: 'bg-tier-1/15 text-tier-1 ring-tier-1/30',
        2: 'bg-tier-2/15 text-tier-2 ring-tier-2/30',
        3: 'bg-tier-3/15 text-tier-3 ring-tier-3/30',
        4: 'bg-tier-4/15 text-tier-4 ring-tier-4/30',
      } as const
    )[tier as 1 | 2 | 3 | 4] ?? 'bg-tier-4/15 text-tier-4 ring-tier-4/30';
  return (
    <span
      className={cn(
        'rounded px-1.5 py-0 font-mono text-[10px] font-medium uppercase tracking-wide ring-1 ring-inset',
        colorClass,
      )}
    >
      T{tier}
    </span>
  );
}

function AtsBadge({ ats }: { ats: string }) {
  const known: Record<string, string> = {
    greenhouse: 'text-ats-greenhouse',
    lever: 'text-ats-lever',
    ashby: 'text-ats-ashby',
    // Workday (PR #33) — no brand color yet, so we just use the
    // muted-foreground token. Explicit entry to make the omission
    // intentional rather than accidental.
    workday: 'text-muted-foreground',
    // iCIMS (PR #55) — same default; will pick up a brand color in a
    // future design pass if/when we settle on one.
    icims: 'text-muted-foreground',
  };
  const colorClass = known[ats.toLowerCase()] ?? 'text-muted-foreground';
  return (
    <span className={cn('font-mono text-[10px] uppercase tracking-wide', colorClass)}>{ats}</span>
  );
}

function RemoteBadge({ remote }: { remote: string }) {
  const cls =
    (
      {
        remote: 'bg-positive/15 text-positive ring-positive/30',
        hybrid: 'bg-pending/15 text-pending ring-pending/30',
        onsite: 'bg-muted text-muted-foreground ring-border',
      } as const
    )[remote as 'remote' | 'hybrid' | 'onsite'] ?? 'bg-muted text-muted-foreground ring-border';
  return (
    <span
      className={cn(
        'rounded px-1.5 py-0 font-mono text-[10px] uppercase tracking-wide ring-1 ring-inset',
        cls,
      )}
    >
      {remote}
    </span>
  );
}

function SalaryChip({
  salary,
}: {
  salary: NonNullable<PostingListItem['salary']>;
}) {
  if (salary.min === null && salary.max === null) return null;
  const min = salary.min ? fmtUsd(salary.min) : null;
  const max = salary.max ? fmtUsd(salary.max) : null;
  const label = min && max ? `${min}–${max}` : (min ?? max);
  return <span className="font-mono text-[12px] text-foreground/80">{label}</span>;
}

function fmtUsd(cents: number): string {
  // Salary is stored as whole dollars per UI_SPEC.md examples (180_000).
  // Render as `$180k` / `$1.2M`.
  if (cents >= 1_000_000) return `$${(cents / 1_000_000).toFixed(1)}M`;
  if (cents >= 1_000) return `$${Math.round(cents / 1_000)}k`;
  return `$${cents}`;
}

function timeAgo(iso: string): string {
  const then = new Date(iso).getTime();
  const now = Date.now();
  const diff = Math.max(0, now - then);
  const minutes = Math.floor(diff / 60_000);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

/**
 * PR 2: pill indicating the posting's current action state when one
 * exists. Only renders for posting.state.current != null — fresh
 * triage cards don't get a pill at all. The visual contract matches
 * the AtsBadge / TierBadge family: mono uppercase 10px with semantic
 * color tokens.
 */
function StatusPill({ state }: { state: ActionType }) {
  // ``ActionType`` includes ``reset`` (the "clear my action on this
  // posting" path) — that's a transient state we never actually want
  // to display as a sticky label, so it falls through to the default
  // muted style. ``rejected`` is a server-side outcome value that the
  // posting.state shape can carry; lowercased here to match the
  // ActionType-or-superset shape the API returns.
  const cls = (
    {
      interested: 'bg-positive/15 text-positive ring-positive/30',
      applied: 'bg-primary/15 text-primary ring-primary/30',
      not_interested: 'bg-muted text-muted-foreground ring-border',
      snoozed: 'bg-pending/15 text-pending ring-pending/30',
      reset: 'bg-muted text-muted-foreground ring-border',
    } satisfies Record<ActionType, string>
  )[state];
  const label = (
    {
      interested: 'INT',
      applied: 'APP',
      not_interested: 'PASS',
      snoozed: 'SNZ',
      reset: '—',
    } satisfies Record<ActionType, string>
  )[state];
  return (
    <span
      data-testid="status-pill"
      aria-label={`Status: ${state}`}
      className={cn(
        'rounded px-1.5 py-0 font-mono text-2xs font-medium uppercase tracking-wide ring-1 ring-inset',
        cls,
      )}
    >
      {label}
    </span>
  );
}

// Tier strip color choice keeps tier-1..4 visually distinct on hover.
function tierStripClass(tier: number): string {
  return (
    (
      {
        1: 'bg-tier-1',
        2: 'bg-tier-2',
        3: 'bg-tier-3',
        4: 'bg-tier-4',
      } as const
    )[tier as 1 | 2 | 3 | 4] ?? 'bg-tier-4'
  );
}
