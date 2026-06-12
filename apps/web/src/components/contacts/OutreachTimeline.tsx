'use client';

/**
 * Outreach message timeline (PR #52).
 *
 * Renders the per-contact outreach history fetched via
 * ``useContactOutreachInfinite`` (newest first). Append-only model — no
 * edit/delete affordances. If the operator logged the wrong thing
 * they log a correction as a new row.
 *
 * Pagination is operator-driven via "Load more" — an accumulating
 * ``useInfiniteQuery`` (fix/audit #5) keeps every loaded page, so a
 * second Load More appends rather than replacing the previous extra
 * window. Initial page is 50 rows (matches the backend's default
 * ``limit``); each Load More fetches the next 50.
 *
 * Layout note: each entry is a flex row — direction icon + channel
 * chip + relative ``sent_at`` + subject (if any) + body preview.
 * Mobile-first: rows wrap at ``sm`` breakpoint when the screen is
 * narrow so the body preview doesn't overflow the panel.
 *
 * source='manual' vs 'gmail_auto' (PR #53): gmail_auto rows will
 * carry an extra dot/badge so the operator can tell which came from
 * inbox-scanning vs manual logging. PR #52 only writes manual; the
 * UI is forward-compatible (renders the chip for any source value).
 */

import { ArrowDownLeft, ArrowUpRight, Link2, Mail, MessageSquare } from 'lucide-react';

import { useContactOutreachInfinite } from '@/lib/api/contacts';
import {
  MESSAGE_CHANNEL_LABELS,
  MESSAGE_DIRECTION_LABELS,
  type MessageChannel,
  type MessageDirection,
  type OutreachMessage,
} from '@/lib/contacts/types';
import { cn } from '@/lib/utils';

export function OutreachTimeline({ contactId }: { contactId: string }) {
  // fix/audit #5: self-fetched accumulating timeline. The component owns the
  // infinite query so Load More appends pages instead of replacing a single
  // extra slot (which dropped the middle page).
  const {
    items: allItems,
    total,
    isLoading,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useContactOutreachInfinite(contactId);

  if (isLoading) {
    return (
      <div className="flex flex-col gap-2" data-testid="outreach-timeline-loading">
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            className="h-12 animate-pulse rounded-md border border-border bg-surface-2"
          />
        ))}
      </div>
    );
  }

  if (allItems.length === 0) {
    return (
      <p
        data-testid="outreach-timeline-empty"
        className="rounded-md border border-dashed border-border bg-surface px-3 py-4 text-center text-[12px] text-muted-foreground"
      >
        No outreach logged yet. Log the first message above.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-2" data-testid="outreach-timeline">
      <ol className="flex flex-col gap-2">
        {allItems.map((m) => (
          <OutreachRow key={m.id} message={m} />
        ))}
      </ol>
      {hasNextPage && (
        <button
          type="button"
          onClick={() => fetchNextPage()}
          disabled={isFetchingNextPage}
          className="self-center rounded-md border border-border bg-surface px-3 py-1 text-[12px] hover:bg-accent disabled:opacity-50"
        >
          {isFetchingNextPage ? 'Loading…' : `Load more (${total - allItems.length} remaining)`}
        </button>
      )}
    </div>
  );
}

function OutreachRow({ message }: { message: OutreachMessage }) {
  const isOutbound = message.direction === 'outbound';
  const dirIcon = isOutbound ? ArrowUpRight : ArrowDownLeft;
  const DirIcon = dirIcon;
  const channelIcon =
    message.channel === 'email' ? Mail : message.channel === 'linkedin' ? Link2 : MessageSquare;
  const ChannelIcon = channelIcon;

  return (
    <li
      data-testid="outreach-row"
      data-direction={message.direction}
      data-channel={message.channel}
      data-source={message.source}
      className="rounded-md border border-border bg-card px-3 py-2 text-[12px]"
    >
      <header className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span
          className={cn(
            'inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium ring-1 ring-inset',
            isOutbound
              ? 'bg-surface-2 text-foreground ring-border'
              : 'bg-positive/15 text-positive ring-positive/30',
          )}
          title={
            (MESSAGE_DIRECTION_LABELS as Record<string, string>)[message.direction] ??
            message.direction
          }
        >
          <DirIcon className="h-3 w-3" aria-hidden="true" />
          {(MESSAGE_DIRECTION_LABELS as Record<string, string>)[
            message.direction as MessageDirection
          ] ?? message.direction}
        </span>
        <span className="inline-flex items-center gap-1 text-muted-foreground">
          <ChannelIcon className="h-3 w-3" aria-hidden="true" />
          {(MESSAGE_CHANNEL_LABELS as Record<string, string>)[message.channel as MessageChannel] ??
            message.channel}
        </span>
        {message.source === 'gmail_auto' && (
          <span
            data-testid="gmail-auto-badge"
            className="rounded bg-muted px-1 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground"
            title="Auto-detected from Gmail"
          >
            auto
          </span>
        )}
        <time className="ml-auto font-mono text-[11px] text-muted-foreground">
          {fmtRelative(message.sent_at)}
        </time>
      </header>
      {message.subject && (
        <p className="mt-1 truncate font-medium text-foreground" title={message.subject}>
          {message.subject}
        </p>
      )}
      {message.body && (
        <p className="mt-0.5 line-clamp-2 text-muted-foreground" title={message.body}>
          {message.body}
        </p>
      )}
    </li>
  );
}

function fmtRelative(iso: string): string {
  const d = new Date(iso);
  const now = Date.now();
  const diffMs = now - d.getTime();
  const diffSec = Math.floor(diffMs / 1000);
  if (diffSec < 60) return `${diffSec}s ago`;
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  if (diffDay < 30) return `${diffDay}d ago`;
  return d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
}
