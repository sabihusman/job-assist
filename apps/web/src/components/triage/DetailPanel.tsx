'use client';

import { ArrowLeft, ExternalLink, Mail, X } from 'lucide-react';
import Link from 'next/link';
import { useEffect, useState } from 'react';

import { ActionButton } from '@/components/shared/ActionButton';
import { CompanyAvatar } from '@/components/shared/CompanyAvatar';
import { MarkdownRenderer } from '@/components/shared/MarkdownRenderer';
import { RepeatSignalBadges } from '@/components/shared/RepeatSignalBadges';
import { ReasonPicker } from '@/components/triage/ReasonPicker';
import { ResumeAttach } from '@/components/triage/ResumeAttach';
import { ScoreBlock } from '@/components/triage/ScoreBlock';
import { StatusButtons } from '@/components/triage/StatusButtons';
import type { TriageCardAction } from '@/components/triage/TriageCard';
import { Sheet, SheetContent, SheetTitle } from '@/components/ui/sheet';
import { useCompanySignals } from '@/lib/api/companySignals';
import { usePosting } from '@/lib/api/hooks';
import { STAGE_LABELS, stageOf } from '@/lib/applied/stages';
import { familyLabel } from '@/lib/triage/family-labels';
import type { GmailOutcomeLink, PostingDetail } from '@/lib/triage/types';
import { useIsLgUp } from '@/lib/use-media-query';
import { cn } from '@/lib/utils';

/**
 * Right-side detail panel.
 *
 * Desktop (≥ lg): in-place aside, 460px wide. Always renders the
 * chrome even with no selection so the column doesn't reflow; the
 * middle swaps between empty / loading / content.
 *
 * Mobile (< lg): full-height Sheet sliding up from the bottom (UX
 * overhaul PR 1). Open state is bound to ``selectedId !== null`` —
 * tapping a card opens the sheet; the Sheet's close button or the
 * backdrop dismisses it via ``onClose``. Pre-PR-1 the detail surface
 * was entirely hidden below lg, leaving mobile users with no detail
 * view at all.
 */
export function DetailPanel({
  selectedId,
  onClose,
  onAction,
  onReasonOpenChange,
}: {
  selectedId: string | null;
  onClose: () => void;
  onAction: (postingId: string, action: TriageCardAction) => void;
  // fix/audit #4: report the in-panel reason picker's open state up so the
  // page can pause its keyboard handler while it's open (otherwise a
  // keypress fires both the picker and the triage shortcut).
  onReasonOpenChange?: (open: boolean) => void;
}) {
  const { data, isLoading } = usePosting(selectedId);
  const isLgUp = useIsLgUp();

  // Choose which body to render based on selection + load state.
  let body: React.ReactNode;
  if (!selectedId) body = <DetailEmptyBody />;
  else if (isLoading || !data) body = <DetailLoadingBody />;
  else
    body = (
      <DetailContentBody
        posting={data}
        onClose={onClose}
        onAction={onAction}
        onReasonOpenChange={onReasonOpenChange}
      />
    );

  // Gate the Sheet ``open`` prop by viewport, not just CSS. Radix
  // Dialog (under Sheet) marks every sibling of its open content
  // with ``aria-hidden="true"`` to enforce a modal trap — that runs
  // even when ``lg:hidden`` hides the visible content, which would
  // silently make the entire FilterRow / Sidebar inaccessible at
  // lg+ (caught in the PR 1 E2E run: ``getByRole('button', { name:
  // 'T1' })`` timed out across 8 specs). Gating by viewport keeps
  // Radix entirely out of the DOM at lg+.
  const sheetOpen = !isLgUp && selectedId !== null;

  return (
    <>
      {/* Desktop in-place panel — visible at lg+.
          PR 2: ``sticky top-12`` (Banner is h-12) keeps the panel in
          view as the operator scrolls the list. Pre-PR-2 the aside was
          a sibling flex child with fixed height but no sticky, so
          clicking a card lower in the list rendered the panel above
          the viewport and forced a scroll-back to see details. */}
      <aside
        data-expanded={selectedId !== null}
        className={cn(
          // Zone separation: a distinct frosted surface (bg-muted/60 +
          // backdrop-blur), a heavier left border, and a soft left shadow so
          // the panel reads as its own plane floating over the list.
          'sticky top-12 hidden h-[calc(100vh-3rem)] shrink-0 flex-col self-start border-l-2 border-border-strong bg-muted/60 shadow-[-12px_0_28px_-18px_rgba(0,0,0,0.18)] backdrop-blur-sm lg:flex',
          // Animated expand-on-select: a neutral resting width that grows when
          // a role is selected and eases back on close. Honors reduced motion.
          'transition-[width] duration-300 ease-in-out motion-reduce:transition-none',
          selectedId !== null ? 'w-[600px]' : 'w-[380px]',
        )}
        aria-label="Posting details"
      >
        {body}
      </aside>

      {/* Mobile sheet — opens when a posting is selected AND the
          viewport is below lg. The ``lg:hidden`` classes on the Sheet
          surfaces are belt-and-suspenders for SSR/hydration: between
          the server paint and ``useEffect`` syncing the media query,
          the open prop is false anyway, so no visible flash. */}
      <Sheet open={sheetOpen} onOpenChange={(o) => !o && onClose()}>
        <SheetContent
          side="bottom"
          className="h-[90vh] p-0 lg:hidden"
          overlayClassName="lg:hidden"
          hideCloseButton
        >
          <SheetTitle className="sr-only">Posting details</SheetTitle>
          {body}
        </SheetContent>
      </Sheet>
    </>
  );
}

function DetailEmptyBody() {
  return (
    <div className="m-auto flex flex-col items-center gap-3 px-8 text-center text-sm text-muted-foreground">
      <ArrowLeft className="h-5 w-5" aria-hidden="true" />
      <p>Select a posting to see details.</p>
    </div>
  );
}

function DetailLoadingBody() {
  return <div className="px-6 py-8 text-sm text-muted-foreground">Loading…</div>;
}

function DetailContentBody({
  posting,
  onClose,
  onAction,
  onReasonOpenChange,
}: {
  posting: PostingDetail;
  onClose: () => void;
  onAction: (postingId: string, action: TriageCardAction) => void;
  onReasonOpenChange?: (open: boolean) => void;
}) {
  const [reasonOpen, setReasonOpen] = useState(false);
  // fix/audit #4: mirror the picker state up to the page keyboard gate.
  // The cleanup resets it to false when this body unmounts (panel closed
  // while the picker was open) so the gate can't get stuck.
  useEffect(() => {
    onReasonOpenChange?.(reasonOpen);
  }, [reasonOpen, onReasonOpenChange]);
  useEffect(() => () => onReasonOpenChange?.(false), [onReasonOpenChange]);
  const { data: signals } = useCompanySignals();
  const company = posting.company;
  const tier = company.tier ?? 4;

  const handlePass = () => setReasonOpen((open) => !open);
  const handlePickReason = (reason: PostingDetail['state_history'][number]['reason']) => {
    setReasonOpen(false);
    if (reason) onAction(posting.id, { kind: 'not_interested', reason });
  };

  return (
    <div className="flex h-full flex-col">
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
        {/* Hero — score-forward: large score block + role title on top,
            company / tier / family beneath. */}
        <div className="flex items-start gap-4">
          <ScoreBlock score={posting.score} size="lg" showLabel className="rounded-md" />
          <div className="flex min-w-0 flex-1 flex-col gap-1.5">
            <h2 className="text-lg font-semibold leading-snug">{posting.role.title}</h2>
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px]">
              <span className="inline-flex min-w-0 items-center gap-1.5">
                <CompanyAvatar name={company.name} size={32} />
                <span className="truncate font-medium text-foreground/90">{company.name}</span>
              </span>
              <span className="inline-flex items-center gap-1 font-mono text-2xs font-medium uppercase tracking-wide text-muted-foreground">
                <span
                  aria-hidden="true"
                  className={cn('h-2 w-2 shrink-0 rounded-full', `bg-tier-${tier}`)}
                />
                T{tier}
              </span>
              <span className="font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
                {posting.role.family ? familyLabel(posting.role.family) : 'Role'}
              </span>
            </div>
            {/* feat/company-app-awareness: company-level "N active apps" /
                "N rejections here" from the Gmail outcome history, matched on
                company name. feat/warm-path-badge: + "N alumni here", clickable
                here (the hero is not inside a button, unlike the list cards) —
                lands on /contacts filtered to this company. */}
            {/* size="lg": the hero badges are a primary signal here — ~1.5x the
                dense list-card scale (which stays sm). */}
            <RepeatSignalBadges
              companyName={company.name}
              signals={signals}
              linkToContacts
              size="lg"
            />
          </div>
        </div>

        {company.description && (
          <p className="mt-4 text-[14px] text-foreground/80">{company.description}</p>
        )}

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
            ) : posting.role.department || posting.role.team ? (
              // The role HAS a department/team but no division row yet — division
              // discovery keys on (company, department, team), so this one is
              // genuinely awaiting the next enrichment sweep.
              <>
                <span aria-hidden="true" className="h-2 w-2 rounded-full bg-pending" />
                <span className="italic text-muted-foreground">
                  Division info pending — will populate after next enrichment run.
                </span>
              </>
            ) : (
              // No department AND no team on the role → division discovery (which
              // requires one of them) can NEVER create a division for it, so don't
              // promise an enrichment that structurally can't run. Hits any role
              // whose ATS didn't surface a department — e.g. Apify-sourced roles.
              <span className="italic text-muted-foreground">
                No business division for this role.
              </span>
            )}
          </div>
        </section>

        {/* feat/application-resume: per-application resume attach (upload a
            .docx/.pdf or paste text). Replaces the apply-time dropdown. */}
        <ResumeAttach postingId={posting.id} resume={posting.resume} />

        {/* feat/manual-application-status: lifecycle status buttons. Marking
            accepted/rejected drops the card out of the Applied tab; rejected
            lands it in Rejected. Gmail rejection shows only as a hint. */}
        <StatusButtons
          postingId={posting.id}
          current={posting.state.resolved_status ?? null}
          companyName={company.name}
          gmailRejectionHint={posting.state.gmail_rejection_hint ?? false}
        />

        {/* feat/applied-pipeline-crosslink: read-only pointer to the matched
            Gmail Pipeline entry. Manual status above stays authoritative; this
            is purely a navigational hint (links to the Pipeline). */}
        {posting.gmail_outcome && <GmailOutcomeChip outcome={posting.gmail_outcome} />}

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
    </div>
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

/**
 * Read-only cross-link to the matched Gmail Pipeline entry (feat/applied-
 * pipeline-crosslink). Informational only — it does NOT change status (manual
 * application_state stays authoritative). Links to the Pipeline view. The match
 * is posting-specific (one email → at-most-one posting), never company-level,
 * so this never reintroduces the fanout bug.
 */
function GmailOutcomeChip({ outcome }: { outcome: GmailOutcomeLink }) {
  const stage = stageOf(outcome.stage);
  const label = stage ? STAGE_LABELS[stage] : outcome.stage;
  const date = new Date(outcome.received_at).toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
  });
  return (
    <Link
      href="/pipeline"
      data-testid="gmail-outcome-link"
      className="mt-3 inline-flex items-center gap-1.5 rounded-md border border-border bg-surface-2 px-2.5 py-1.5 text-[12px] text-muted-foreground transition-colors hover:border-border-strong hover:text-foreground"
      title="View in the Gmail Pipeline"
    >
      <Mail className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      <span>
        Gmail: <span className="font-medium text-foreground/90">{label}</span> · {date}
      </span>
      <span aria-hidden="true">→</span>
    </Link>
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
