'use client';

import { Building2, Clock, DollarSign, MapPin } from 'lucide-react';

import { ActionButton } from '@/components/shared/ActionButton';
import { RepeatSignalBadges } from '@/components/shared/RepeatSignalBadges';
import { ReasonPicker } from '@/components/triage/ReasonPicker';
import { ScoreBlock, isDimScore } from '@/components/triage/ScoreBlock';
import type { RepeatSignals } from '@/lib/api/companySignals';
import { familyLabel } from '@/lib/triage/family-labels';
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
  signals,
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
  // feat/company-app-awareness: the per-company signal map (keyed by normalized
  // company name), passed down so the card can badge how many open apps /
  // rejections the operator has at this company — visible at triage time,
  // before investing in the role. Optional so other surfaces reusing the card
  // (and existing tests) stay valid.
  signals?: RepeatSignals;
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
  // Score-forward restyle: ≤40 reads as dismissible — mute the body so a
  // wall of low scores recedes at a glance. The score block itself stays
  // full-strength (gray band) so the number is still legible.
  const dim = isDimScore(posting.score);

  return (
    <div className="group relative">
      <article
        data-selected={isSelected}
        className={cn(
          'flex items-stretch overflow-hidden rounded-md border bg-card shadow-card transition-colors',
          isSelected
            ? 'border-border-strong ring-1 ring-primary'
            : 'border-border hover:border-border-strong hover:bg-accent/20',
        )}
      >
        {/* Tier strip — a thin tier-colored left edge; selected overrides to
          primary teal. (Also the regression anchor for the tier test.) */}
        <span
          aria-hidden="true"
          data-testid="tier-strip"
          className={cn('w-0.5 shrink-0', isSelected ? 'bg-primary' : tierColorClass)}
        />

        {/* feat/bulk-triage-actions: per-card multi-select checkbox. stopPropagation
          keeps a checkbox click from also triggering the card-select button. */}
        {onToggleCheck && (
          <div className="flex shrink-0 items-start pl-3 pt-3">
            <input
              type="checkbox"
              checked={isChecked}
              aria-label={`Select ${company.name} — ${role.title}`}
              onChange={onToggleCheck}
              onClick={(e) => e.stopPropagation()}
              className="h-4 w-4 cursor-pointer accent-primary"
            />
          </div>
        )}

        {/* SCORE BLOCK — the dominant left rail (score-forward restyle). */}
        <ScoreBlock score={posting.score} size="md" className="self-stretch" />

        {/* Card body — clicking selects, but the action column is excluded
          via stopPropagation in its own buttons. */}
        <button
          type="button"
          onClick={onSelect}
          className={cn(
            'flex min-w-0 flex-1 flex-col gap-1 px-4 py-3 text-left',
            dim && 'opacity-60',
          )}
          aria-label={`Open detail for ${company.name} — ${role.title}`}
        >
          {/* Line 1 — role title (truncated; full title on hover via title=)
            with the status pill pinned right. */}
          <div className="flex items-center gap-2">
            <span className="min-w-0 flex-1 truncate text-base font-semibold" title={role.title}>
              {role.title}
            </span>
            {posting.state.current && <StatusPill state={posting.state.current} />}
          </div>

          {/* Line 2 — tier dot+label, family tag, and company-level app
            awareness (active apps / rejections here). */}
          <div className="flex flex-wrap items-center gap-2">
            <TierBadge tier={tier} />
            <FamilyTag family={role.family} />
            {remote && <RemoteBadge remote={String(remote)} />}
            <RepeatSignalBadges companyName={company.name} signals={signals} />
          </div>

          {/* Line 3 — metadata: company / location / salary / first-seen /
            source, each icon+label (mono on the numeric ones). */}
          <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-0.5 text-sm text-muted-foreground">
            <Meta icon={Building2}>
              <span className="truncate">{company.name}</span>
            </Meta>
            {posting.location_raw && (
              <Meta icon={MapPin}>
                <span className="truncate">{posting.location_raw}</span>
              </Meta>
            )}
            {posting.salary && (
              <Meta icon={DollarSign}>
                <SalaryChip salary={posting.salary} />
              </Meta>
            )}
            <Meta icon={Clock}>
              <span className="font-mono text-xs">{timeAgo(posting.first_seen_at)}</span>
            </Meta>
            <AtsBadge ats={posting.source.ats} />
          </div>
        </button>

        {/* Action column — hover-reveals (kept in the DOM + focusable so
          keyboard and tests still reach it). */}
        <div
          className="flex items-start gap-1.5 px-3 py-3 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100"
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
      </article>

      {/* Inline reason picker — only rendered when expanded so its
          keyboard listener doesn't compete with the page-level one
          (the page-level hook is also paused via ``enabled=false``
          when ``reasonOpen`` is true; this is belt-and-suspenders).
          Lives OUTSIDE the ``overflow-hidden`` article so it isn't
          clipped, anchored to this relative wrapper. */}
      {reasonOpen && (
        <div className="absolute inset-x-4 top-full z-20 -mt-1 rounded-md border border-border bg-surface-2 p-3 shadow-card">
          <ReasonPicker onSelect={handlePickReason} onCancel={onToggleReason} />
        </div>
      )}
    </div>
  );
}

// ── Sub-presentation pieces ─────────────────────────────────────────────

/** Tier as a colored dot + mono label (score-forward restyle). */
function TierBadge({ tier }: { tier: number }) {
  const dotClass =
    (
      {
        1: 'bg-tier-1',
        2: 'bg-tier-2',
        3: 'bg-tier-3',
        4: 'bg-tier-4',
      } as const
    )[tier as 1 | 2 | 3 | 4] ?? 'bg-tier-4';
  return (
    <span className="inline-flex items-center gap-1 font-mono text-2xs font-medium uppercase tracking-wide text-muted-foreground">
      <span aria-hidden="true" className={cn('h-2 w-2 shrink-0 rounded-full', dotClass)} />T{tier}
    </span>
  );
}

/** Role family as a mono uppercase pill. Maps OUR real role_family values. */
function FamilyTag({ family }: { family: string | null }) {
  if (!family) return null;
  return (
    <span className="rounded bg-muted px-1.5 py-0 font-mono text-2xs font-medium uppercase tracking-wide text-muted-foreground ring-1 ring-inset ring-border">
      {familyLabel(family)}
    </span>
  );
}

/** A metadata cell: small leading icon + value. */
function Meta({
  icon: Icon,
  children,
}: {
  icon: React.ComponentType<{ className?: string; 'aria-hidden'?: boolean }>;
  children: React.ReactNode;
}) {
  return (
    <span className="inline-flex min-w-0 items-center gap-1">
      <Icon className="h-3 w-3 shrink-0" aria-hidden={true} />
      {children}
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
