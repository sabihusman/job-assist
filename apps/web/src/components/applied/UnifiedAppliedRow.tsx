'use client';

import { ChevronRight, Mail, SquareArrowOutUpRight } from 'lucide-react';
import Link from 'next/link';
import { useState } from 'react';

import { StatusButtons } from '@/components/triage/StatusButtons';
import { STAGE_LABELS, stageOf } from '@/lib/applied/stages';
import {
  type AppliedSource,
  type UnifiedAppliedEntry,
  entryStage,
  entryStatusLabel,
  entryTone,
} from '@/lib/applied/unify';
import { cn } from '@/lib/utils';

/**
 * One row in the unified Applied list (feat/applied-unified). Unlike the old
 * AppliedRow (one manual posting), an entry here can come from Gmail, a manual
 * application_state, or both fused. The status badge shows the AUTHORITATIVE
 * status (manual overlay wins; else the latest Gmail stage). Expanding reveals
 * the Gmail timeline (already loaded — no per-row fetch) and, when the entry
 * maps to a corpus posting, the manual StatusButtons + a Triage deep-link.
 */
export function UnifiedAppliedRow({ entry }: { entry: UnifiedAppliedEntry }) {
  const [open, setOpen] = useState(false);
  const at = new Date(entry.at);

  return (
    <li className="rounded-md border border-border bg-card shadow-card">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className={cn('flex w-full items-center gap-3 px-4 py-3 text-left', 'hover:bg-accent/30')}
        data-testid="unified-applied-row"
      >
        <ChevronRight
          aria-hidden="true"
          className={cn(
            'h-4 w-4 shrink-0 text-muted-foreground transition-transform',
            open && 'rotate-90',
          )}
        />
        <SourceChip source={entry.source} />
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <div className="flex flex-wrap items-center gap-2 text-[14px] font-semibold">
            <span className="truncate">{entry.company}</span>
            {entry.role && (
              <>
                <span aria-hidden="true" className="text-muted-foreground">
                  ·
                </span>
                <span className="truncate text-foreground/90">{entry.role}</span>
              </>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2 text-[12px] text-muted-foreground">
            <span>
              {entry.source === 'manual' ? 'applied' : 'last activity'} {fmtMonthDay(at)}
            </span>
            <span aria-hidden="true">·</span>
            <span className="font-mono text-[11px]">{fmtRelative(at)}</span>
            {entry.events.length > 0 && (
              <>
                <span aria-hidden="true">·</span>
                <span className="inline-flex items-center gap-1 font-mono text-[11px]">
                  <Mail className="h-3 w-3" aria-hidden="true" />
                  {entry.events.length}
                </span>
              </>
            )}
          </div>
        </div>
        <StatusPill entry={entry} />
      </button>

      {open && (
        <div className="border-t border-border bg-surface-2/50 px-4 py-3">
          {entry.postingId && (
            <div className="mb-3 flex flex-col gap-2">
              {/* Manual lifecycle control — only when a corpus posting backs this
                  entry. manualStatus is the authoritative current value. */}
              <StatusButtons
                postingId={entry.postingId}
                current={entry.manualStatus}
                companyName={entry.company}
                gmailRejectionHint={false}
              />
              <Link
                href={`/?posting=${entry.postingId}`}
                data-testid="unified-view-posting-link"
                className="inline-flex w-fit items-center gap-1 text-[12px] text-primary underline-offset-2 hover:underline"
                title="Open the matched role in Triage"
              >
                <SquareArrowOutUpRight className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                View matched role
              </Link>
            </div>
          )}

          {entry.events.length > 0 ? (
            <>
              <h4 className="mb-3 mt-1 font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
                Gmail timeline
              </h4>
              <ol className="relative flex flex-col gap-3 pl-4">
                <span
                  aria-hidden="true"
                  className="absolute bottom-1 left-[5px] top-1 w-px bg-border"
                />
                {entry.events.map((e) => {
                  const stage = stageOf(e.stage);
                  return (
                    <li key={e.id} className="relative flex flex-col gap-0.5 text-[13px]">
                      <span
                        aria-hidden="true"
                        className="absolute left-[-7px] top-1.5 h-2 w-2 rounded-full bg-primary"
                      />
                      <div className="flex items-center gap-2 text-muted-foreground">
                        <span>{stage ? STAGE_LABELS[stage] : e.stage}</span>
                        <span aria-hidden="true">·</span>
                        <span className="font-mono text-[11px]">
                          {fmtMonthDay(new Date(e.received_at))}
                        </span>
                      </div>
                      {e.subject && (
                        <span className="text-[12px] text-foreground/80">{e.subject}</span>
                      )}
                    </li>
                  );
                })}
              </ol>
            </>
          ) : (
            <p className="text-[12px] text-muted-foreground">
              No Gmail activity matched to this application yet.
            </p>
          )}
        </div>
      )}
    </li>
  );
}

const SOURCE_META: Record<AppliedSource, { label: string; cls: string; title: string }> = {
  both: {
    label: 'Manual + Gmail',
    cls: 'bg-positive/15 text-positive ring-positive/30',
    title: 'You set a manual status and Gmail tracked this application',
  },
  manual: {
    label: 'Manual',
    cls: 'bg-primary/15 text-primary ring-primary/30',
    title: 'You manually marked this applied; no Gmail email matched it',
  },
  gmail: {
    label: 'Gmail',
    cls: 'bg-muted text-muted-foreground ring-border',
    title: 'Detected from your Gmail; no manual status set',
  },
};

function SourceChip({ source }: { source: AppliedSource }) {
  const m = SOURCE_META[source];
  return (
    <span
      title={m.title}
      data-testid={`source-chip-${source}`}
      className={cn(
        'shrink-0 rounded px-1.5 py-0 font-mono text-[10px] font-medium uppercase tracking-wide ring-1 ring-inset',
        m.cls,
      )}
    >
      {m.label}
    </span>
  );
}

const TONE_CLASSES = {
  positive: 'bg-positive/15 text-positive ring-positive/30',
  negative: 'bg-negative/15 text-negative ring-negative/30',
  pending: 'bg-pending/15 text-pending ring-pending/30',
  muted: 'bg-muted text-muted-foreground ring-border',
} as const;

/** Status badge showing the AUTHORITATIVE status: manual label when set, else
 *  the Gmail stage label. Tone follows the resolved stage. */
function StatusPill({ entry }: { entry: UnifiedAppliedEntry }) {
  const label = entryStatusLabel(entry) ?? STAGE_LABELS[entryStage(entry)];
  return (
    <span
      data-testid="unified-status-pill"
      className={cn(
        'shrink-0 rounded px-1.5 py-0 text-[10px] font-medium ring-1 ring-inset',
        TONE_CLASSES[entryTone(entry)],
      )}
    >
      {label}
    </span>
  );
}

function fmtMonthDay(d: Date): string {
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function fmtRelative(d: Date): string {
  const diff = Math.max(0, Date.now() - d.getTime());
  const minutes = Math.floor(diff / 60_000);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}
