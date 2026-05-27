'use client';

import { ArrowLeft, ExternalLink, X } from 'lucide-react';
import { useState } from 'react';

import { ActionButton } from '@/components/shared/ActionButton';
import { CompanyAvatar } from '@/components/shared/CompanyAvatar';
import { MarkdownRenderer } from '@/components/shared/MarkdownRenderer';
import { ReasonPicker } from '@/components/triage/ReasonPicker';
import type { TriageCardAction } from '@/components/triage/TriageCard';
import { usePosting } from '@/lib/api/hooks';
import { familyLabel } from '@/lib/triage/family-labels';
import type { PostingDetail } from '@/lib/triage/types';
import { cn } from '@/lib/utils';

/**
 * Right-side detail panel. 460px wide, sticky inside its own column,
 * visible at lg+ only — the parent layout hides it on smaller screens.
 *
 * Always renders the chrome (top mini header, action bar) even with
 * no selection so the column doesn't reflow. The middle is replaced
 * with an empty-state message when `selectedId` is null or while the
 * detail query resolves.
 */
export function DetailPanel({
  selectedId,
  onClose,
  onAction,
}: {
  selectedId: string | null;
  onClose: () => void;
  onAction: (postingId: string, action: TriageCardAction) => void;
}) {
  const { data, isLoading } = usePosting(selectedId);

  if (!selectedId) return <DetailEmpty />;
  if (isLoading || !data) return <DetailLoading />;
  return <DetailContent posting={data} onClose={onClose} onAction={onAction} />;
}

function DetailEmpty() {
  return (
    <aside className="hidden h-[calc(100vh-3rem)] w-[460px] shrink-0 border-l border-border bg-surface lg:flex">
      <div className="m-auto flex flex-col items-center gap-3 px-8 text-center text-sm text-muted-foreground">
        <ArrowLeft className="h-5 w-5" aria-hidden="true" />
        <p>Select a posting to see details.</p>
      </div>
    </aside>
  );
}

function DetailLoading() {
  return (
    <aside className="hidden h-[calc(100vh-3rem)] w-[460px] shrink-0 border-l border-border bg-surface lg:block">
      <div className="px-6 py-8 text-sm text-muted-foreground">Loading…</div>
    </aside>
  );
}

function DetailContent({
  posting,
  onClose,
  onAction,
}: {
  posting: PostingDetail;
  onClose: () => void;
  onAction: (postingId: string, action: TriageCardAction) => void;
}) {
  const [reasonOpen, setReasonOpen] = useState(false);
  const company = posting.company;
  const tier = company.tier ?? 4;

  const handlePass = () => setReasonOpen((open) => !open);
  const handlePickReason = (reason: PostingDetail['state_history'][number]['reason']) => {
    setReasonOpen(false);
    if (reason) onAction(posting.id, { kind: 'not_interested', reason });
  };

  return (
    <aside className="hidden h-[calc(100vh-3rem)] w-[460px] shrink-0 flex-col border-l border-border bg-surface lg:flex">
      {/* Top mini header */}
      <div className="sticky top-0 z-10 flex h-10 items-center gap-2 border-b border-border bg-surface px-4">
        <span
          className={cn(
            'rounded px-1.5 py-0 font-mono text-[10px] font-medium uppercase tracking-wide ring-1 ring-inset',
            `bg-tier-${tier}/15 text-tier-${tier} ring-tier-${tier}/30`,
          )}
        >
          T{tier}
        </span>
        <span className="flex-1 truncate text-sm font-semibold">{company.name}</span>
        {posting.source.url && (
          <a
            href={posting.source.url}
            target="_blank"
            rel="noreferrer noopener"
            aria-label="Open job description in new tab"
            className="inline-flex h-7 w-7 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            <ExternalLink className="h-3.5 w-3.5" />
          </a>
        )}
        <button
          type="button"
          onClick={onClose}
          aria-label="Close detail panel"
          className="inline-flex h-7 w-7 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* Scroll region */}
      <div className="flex-1 overflow-y-auto px-6 py-6">
        {/* Hero */}
        <div className="flex items-start gap-4">
          <CompanyAvatar name={company.name} size={56} />
          <div className="flex flex-col gap-1">
            <h3 className="text-[16px] font-semibold">{company.name}</h3>
            <span className="font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
              {posting.role.family ? familyLabel(posting.role.family) : 'Role'}
            </span>
          </div>
        </div>

        {company.description && (
          <p className="mt-4 text-[14px] text-foreground/80">{company.description}</p>
        )}

        <h2 className="mt-6 text-[14px] font-semibold">{posting.role.title}</h2>

        {/* Key/value grid */}
        <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 text-[12px]">
          <KvRow
            label="Locations"
            value={
              posting.locations_normalized.length > 0
                ? posting.locations_normalized.join(', ')
                : (posting.location_raw ?? '—')
            }
          />
          <KvRow label="Remote" value={posting.remote_type ? String(posting.remote_type) : '—'} />
          <KvRow label="Salary" value={fmtSalary(posting.salary)} mono />
          <KvRow label="Source" value={posting.source.ats.toUpperCase()} />
          <KvRow label="First seen" value={fmtAgo(posting.first_seen_at)} mono />
          {/* PR #76: previously hardcoded ``value="—"`` regardless of
              ``posting.score``. Same source as the card's FitScoreBadge
              (``/postings/{id}`` returns ``score: jp.fit_score`` —
              ``main.py:2080``). Null when the score sweep hasn't visited
              the row yet; render the em-dash placeholder in that case. */}
          <KvRow label="Score" value={posting.score !== null ? String(posting.score) : '—'} mono />
          <KvRow label="Family" value={familyLabel(posting.role.family)} />
          <KvRow label="ID" value={posting.id.slice(0, 8)} mono />
        </dl>

        {/* Division section */}
        <section className="mt-6">
          <h4 className="font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
            Business division for this role
          </h4>
          <div className="mt-2 flex items-center gap-2 rounded-md border border-border bg-surface-2 p-3 text-[13px]">
            {posting.division ? (
              <div className="flex flex-col gap-1">
                <span className="font-semibold">
                  {posting.division.department}
                  {posting.division.team ? ` · ${posting.division.team}` : ''}
                </span>
                {posting.division.description && (
                  <span className="text-muted-foreground">{posting.division.description}</span>
                )}
              </div>
            ) : (
              <>
                <span aria-hidden="true" className="h-2 w-2 rounded-full bg-pending" />
                <span className="italic text-muted-foreground">
                  Division info pending — will populate after next enrichment run.
                </span>
              </>
            )}
          </div>
        </section>

        {/* JD markdown — summary preferred, full description on toggle.
            Keyed on posting.id so the showFullJd state resets whenever
            the operator selects a different posting. */}
        <JdSection
          key={posting.id}
          summary={posting.jd_summary_markdown}
          fullText={posting.description_markdown}
        />
      </div>

      {/* Sticky action bar */}
      <div className="sticky bottom-0 flex flex-col gap-2 border-t border-border bg-surface px-4 py-3">
        {reasonOpen && (
          <ReasonPicker onSelect={handlePickReason} onCancel={() => setReasonOpen(false)} />
        )}
        <div className="flex gap-2">
          <ActionButton
            variant="interested"
            size="full"
            onClick={() => onAction(posting.id, { kind: 'interested' })}
          />
          <ActionButton variant="pass" size="full" onClick={handlePass} />
          <ActionButton
            variant="applied"
            size="full"
            onClick={() => onAction(posting.id, { kind: 'applied' })}
          />
          <ActionButton
            variant="snooze"
            size="full"
            onClick={() => onAction(posting.id, { kind: 'snoozed' })}
          />
        </div>
      </div>
    </aside>
  );
}

/**
 * JD section. Three states:
 *   1. summary present → render summary, toggle reveals full description below
 *   2. summary null, full present → render full + "summary pending" footnote
 *   3. both null → empty-state line
 *
 * Toggle state is *intentionally* not lifted: the parent re-keys this
 * component on posting.id, so showFullJd resets implicitly on selection
 * change without needing a useEffect.
 */
function JdSection({
  summary,
  fullText,
}: {
  summary: string | null;
  fullText: string | null;
}) {
  const [showFullJd, setShowFullJd] = useState(false);

  if (summary) {
    return (
      <section className="mt-6">
        <h4 className="font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
          Job description (summary)
        </h4>
        <MarkdownRenderer source={summary} className="prose-jd mt-3" />
        {fullText && (
          <>
            <button
              type="button"
              onClick={() => setShowFullJd((open) => !open)}
              aria-expanded={showFullJd}
              className="mt-3 inline-flex items-center gap-1 rounded text-[12px] text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
            >
              {showFullJd ? 'Hide full description ↑' : 'Show full description ↓'}
            </button>
            {showFullJd && (
              <div className="mt-3 border-t border-border pt-3">
                <h5 className="font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
                  Full description
                </h5>
                <MarkdownRenderer source={fullText} className="prose-jd mt-2" />
              </div>
            )}
          </>
        )}
      </section>
    );
  }

  if (fullText) {
    return (
      <section className="mt-6">
        <h4 className="font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
          Job description
        </h4>
        <MarkdownRenderer source={fullText} className="prose-jd mt-3" />
        <p className="mt-3 text-[11px] italic text-muted-foreground">
          Summary pending — will populate after next enrichment run.
        </p>
      </section>
    );
  }

  return (
    <section className="mt-6">
      <h4 className="font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
        Job description
      </h4>
      <p className="mt-3 text-[13px] text-muted-foreground">No description available.</p>
    </section>
  );
}

function KvRow({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-[12px] text-muted-foreground">{label}</dt>
      <dd className={cn('text-[13px] text-foreground/90', mono && 'font-mono text-[12px]')}>
        {value}
      </dd>
    </div>
  );
}

function fmtSalary(salary: PostingDetail['salary']): string {
  if (!salary) return '—';
  const min = salary.min ? formatUsd(salary.min) : null;
  const max = salary.max ? formatUsd(salary.max) : null;
  if (min && max) return `${min}–${max}`;
  return min ?? max ?? '—';
}

function formatUsd(n: number): string {
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `$${Math.round(n / 1_000)}k`;
  return `$${n}`;
}

function fmtAgo(iso: string): string {
  const then = new Date(iso).getTime();
  const diff = Math.max(0, Date.now() - then);
  const minutes = Math.floor(diff / 60_000);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}
