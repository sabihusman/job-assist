'use client';

import { ArrowLeft, SquareArrowOutUpRight, X } from 'lucide-react';
import Link from 'next/link';

import { CompanyAvatar } from '@/components/shared/CompanyAvatar';
import { Sheet, SheetContent, SheetTitle } from '@/components/ui/sheet';
import { useAllOutcomes } from '@/lib/api/applied';
import { STAGE_LABELS, stageBadgeTone, stageOf } from '@/lib/applied/stages';
import type { OutcomeEvent } from '@/lib/applied/types';
import { companyFromSubject, roleFromSubject } from '@/lib/pipeline/companyFromSubject';
import { useIsLgUp } from '@/lib/use-media-query';
import { cn } from '@/lib/utils';

/**
 * Pipeline card detail panel (feat/pipeline-detail). Mirrors
 * triage/DetailPanel: in-place aside ≥ lg, bottom Sheet on mobile, keyed on the
 * selected card id.
 *
 * Capped by stored data: there is NO email body — only subject + a ~200-char
 * Gmail snippet — so we display those raw (no LLM summary). The role is shown
 * only when extractable from a subject (~23% of the time) and OMITTED
 * otherwise. The conversation arc for a multi-event thread is derived entirely
 * from the already-cached useAllOutcomes set, filtered by the card's thread id.
 */
export function PipelineDetailPanel({
  selectedId,
  onClose,
}: {
  selectedId: string | null;
  onClose: () => void;
}) {
  const { data, isLoading } = useAllOutcomes(true);
  const isLgUp = useIsLgUp();

  const events = selectedId ? eventsForCard(selectedId, data?.items ?? []) : [];

  let body: React.ReactNode;
  if (!selectedId) body = <EmptyBody />;
  else if (isLoading && events.length === 0) body = <LoadingBody />;
  else if (events.length === 0) body = <MissingBody onClose={onClose} />;
  else body = <ContentBody events={events} onClose={onClose} />;

  const sheetOpen = !isLgUp && selectedId !== null;

  return (
    <>
      <aside
        className="sticky top-12 hidden h-[calc(100vh-3rem)] w-[420px] shrink-0 flex-col self-start border-l border-border bg-surface lg:flex lg:animate-in lg:slide-in-from-right lg:duration-200"
        aria-label="Application details"
      >
        {body}
      </aside>
      <Sheet open={sheetOpen} onOpenChange={(o) => !o && onClose()}>
        <SheetContent
          side="bottom"
          className="h-[85vh] p-0 lg:hidden"
          overlayClassName="lg:hidden"
          hideCloseButton
        >
          <SheetTitle className="sr-only">Application details</SheetTitle>
          {body}
        </SheetContent>
      </Sheet>
    </>
  );
}

/** Map a bucketOutcomes card id ("t:<threadId>" | "o:<outcomeId>") back to its
 *  outcome rows from the cached set. */
function eventsForCard(cardId: string, outcomes: readonly OutcomeEvent[]): OutcomeEvent[] {
  let matched: OutcomeEvent[];
  if (cardId.startsWith('t:')) {
    const threadId = cardId.slice(2);
    matched = outcomes.filter((o) => o.email_thread_id === threadId);
  } else if (cardId.startsWith('o:')) {
    const outcomeId = cardId.slice(2);
    matched = outcomes.filter((o) => o.id === outcomeId);
  } else {
    matched = [];
  }
  // Chronological (applied → screen → rejected).
  return [...matched].sort((a, b) => Date.parse(a.received_at) - Date.parse(b.received_at));
}

function deriveLabel(events: readonly OutcomeEvent[]): string {
  const linked = events.find((e) => e.company_name)?.company_name;
  if (linked) return linked;
  for (const e of events) {
    const c = companyFromSubject(e.subject);
    if (c) return c;
  }
  return events[events.length - 1]?.from_domain ?? events[0]?.subject ?? 'Application';
}

function deriveRole(events: readonly OutcomeEvent[]): string | null {
  for (const e of events) {
    const r = roleFromSubject(e.subject);
    if (r) return r;
  }
  return null;
}

function EmptyBody() {
  return (
    <div className="m-auto flex flex-col items-center gap-3 px-8 text-center text-sm text-muted-foreground">
      <ArrowLeft className="h-5 w-5" aria-hidden="true" />
      <p>Select a card to see the application.</p>
    </div>
  );
}

function LoadingBody() {
  return <div className="px-6 py-8 text-sm text-muted-foreground">Loading…</div>;
}

function MissingBody({ onClose }: { onClose: () => void }) {
  return (
    <div className="flex h-full flex-col">
      <PanelHeader title="—" onClose={onClose} />
      <div className="px-6 py-8 text-sm text-muted-foreground">
        This application's emails are no longer in the loaded set.
      </div>
    </div>
  );
}

function ContentBody({
  events,
  onClose,
}: {
  events: readonly OutcomeEvent[];
  onClose: () => void;
}) {
  const latest = events[events.length - 1];
  const company = deriveLabel(events);
  const role = deriveRole(events);
  const stage = stageOf(latest.stage);
  // feat/applied-pipeline-crosslink: if any email here was matched to a SPECIFIC
  // corpus posting (outcome_event.job_posting_id), link to it. Posting-specific
  // by construction — never company-level.
  const linkedPostingId = events.find((e) => e.posting_id)?.posting_id ?? null;

  return (
    <div className="flex h-full flex-col">
      <PanelHeader title={company} onClose={onClose} stage={stage} />
      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="flex items-start gap-4">
          <CompanyAvatar name={company} size={56} />
          <div className="flex flex-col gap-1">
            <h3 className="text-[16px] font-semibold">{company}</h3>
            {/* Role OMITTED when not extractable — never promised. */}
            {role && (
              <span className="text-[13px] text-muted-foreground" data-testid="detail-role">
                {role}
              </span>
            )}
            <span className="font-mono text-[11px] text-muted-foreground">
              {events.length === 1
                ? fmtDate(latest.received_at)
                : `${events.length} emails · ${fmtDate(events[0].received_at)} – ${fmtDate(latest.received_at)}`}
            </span>
            {linkedPostingId && (
              <Link
                href={`/?posting=${linkedPostingId}`}
                data-testid="view-posting-link"
                className="mt-1 inline-flex items-center gap-1 text-[12px] text-primary underline-offset-2 hover:underline"
                title="Open the matched role in Triage"
              >
                <SquareArrowOutUpRight className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                View matched role
              </Link>
            )}
          </div>
        </div>

        {/* Conversation timeline (applied → screen → rejected). One block per
            outcome event; for a single-event card it's just the one email. */}
        <section className="mt-6">
          <h4 className="font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
            {events.length > 1 ? 'Conversation' : 'Email'}
          </h4>
          <ol className="mt-3 flex list-none flex-col gap-3 p-0">
            {events.map((e) => (
              <TimelineItem key={e.id} event={e} />
            ))}
          </ol>
        </section>
      </div>
    </div>
  );
}

function TimelineItem({ event }: { event: OutcomeEvent }) {
  const stage = stageOf(event.stage);
  return (
    <li className="rounded-md border border-border bg-card p-3">
      <div className="flex items-center justify-between gap-2">
        {stage ? (
          <StageDot stage={stage} />
        ) : (
          <span className="text-[11px] text-muted-foreground">{event.stage}</span>
        )}
        <span className="font-mono text-[11px] text-muted-foreground">
          {fmtDate(event.received_at)}
        </span>
      </div>
      <p className="mt-1.5 text-[13px] font-medium">{event.subject}</p>
      {event.raw_snippet && (
        <p className="mt-1 text-[12px] text-muted-foreground">{event.raw_snippet}</p>
      )}
    </li>
  );
}

function StageDot({ stage }: { stage: NonNullable<ReturnType<typeof stageOf>> }) {
  const tone = stageBadgeTone(stage);
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-wide',
        tone === 'positive' && 'text-positive',
        tone === 'negative' && 'text-negative',
        tone === 'pending' && 'text-pending',
        tone === 'muted' && 'text-muted-foreground',
      )}
    >
      <span aria-hidden="true" className="h-2 w-2 rounded-full bg-current" />
      {STAGE_LABELS[stage]}
    </span>
  );
}

function PanelHeader({
  title,
  onClose,
  stage,
}: {
  title: string;
  onClose: () => void;
  stage?: ReturnType<typeof stageOf>;
}) {
  return (
    <div className="sticky top-0 z-10 flex h-10 items-center gap-2 border-b border-border bg-surface px-4">
      <span className="flex-1 truncate text-sm font-semibold">{title}</span>
      {stage && (
        <span className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
          {STAGE_LABELS[stage]}
        </span>
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
  );
}

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}
