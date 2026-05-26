'use client';

import { ExternalLink, Mail } from 'lucide-react';

import {
  CONTACT_SOURCE_LABELS,
  type ContactListItem,
  type ContactSourceType,
} from '@/lib/contacts/types';
import { cn } from '@/lib/utils';

/**
 * Read-only contacts table (PR #51).
 *
 * No archive button per row — that ships in PR #52 with full CRUD.
 * The ``include archived`` toggle is a VIEW control over already-
 * archived rows, set at the page level and reflected in the API call.
 *
 * PII discipline: this component renders names + emails for the
 * operator's own data. It does NOT log row contents anywhere.
 *
 * Columns: name · title · company · source chip · contact channels ·
 * created date. ``preferred_first_name`` is surfaced inline with the
 * legal first name when present (``"Robert (Bobby) Smith"``).
 */

export function ContactsTable({
  contacts,
  showingArchived,
  onOpenDetail,
  selectedId,
}: {
  contacts: readonly ContactListItem[];
  showingArchived: boolean;
  /** Optional — when provided, rows become clickable and open the detail panel. */
  onOpenDetail?: (contactId: string) => void;
  /** Optional — visually highlights the currently-open contact's row. */
  selectedId?: string | null;
}) {
  if (contacts.length === 0) {
    return (
      <section
        data-testid="contacts-empty"
        className="flex flex-col items-center gap-2 rounded-md border border-border bg-card px-6 py-12 text-center"
      >
        <h2 className="text-sm font-semibold">
          {showingArchived ? 'No contacts in this view.' : 'No contacts yet.'}
        </h2>
        <p className="text-[13px] text-muted-foreground">
          Seed the contact table via{' '}
          <code className="rounded bg-surface-2 px-1 py-0.5 font-mono text-[11px]">
            POST /admin/seed/contacts
          </code>{' '}
          (see RUNBOOK.md).
        </p>
      </section>
    );
  }

  return (
    <div className="overflow-x-auto rounded-md border border-border bg-card">
      <table className="w-full border-collapse text-[13px]">
        <thead>
          <tr className="border-b border-border bg-surface-2/50 text-left">
            <Th>Name</Th>
            <Th>Title · Company</Th>
            <Th>Source</Th>
            <Th>Channels</Th>
            <Th>Added</Th>
          </tr>
        </thead>
        <tbody>
          {contacts.map((c) => (
            <ContactRow
              key={c.id}
              contact={c}
              onOpenDetail={onOpenDetail}
              isSelected={selectedId === c.id}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th className="px-3 py-2 font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
      {children}
    </th>
  );
}

function ContactRow({
  contact,
  onOpenDetail,
  isSelected,
}: {
  contact: ContactListItem;
  onOpenDetail?: (contactId: string) => void;
  isSelected?: boolean;
}) {
  const fullName = formatFullName(contact);
  const openFn = onOpenDetail;
  const clickable = openFn !== undefined;
  // Keyboard: Enter / Space open the panel when the row has focus.
  const handleKey = (e: React.KeyboardEvent<HTMLTableRowElement>) => {
    if (!openFn) return;
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      openFn(contact.id);
    }
  };
  // The email/LinkedIn cell contains its own anchors. Use stopPropagation
  // there so clicking those links opens the link, not the panel.
  return (
    <tr
      data-testid="contact-row"
      data-archived={contact.archived_at !== null}
      data-selected={isSelected ? 'true' : 'false'}
      role={clickable ? 'button' : undefined}
      tabIndex={clickable ? 0 : undefined}
      aria-label={clickable ? `Open detail for ${fullName}` : undefined}
      onClick={openFn ? () => openFn(contact.id) : undefined}
      onKeyDown={openFn ? handleKey : undefined}
      className={cn(
        'border-b border-border/60 hover:bg-accent/30',
        clickable && 'cursor-pointer',
        isSelected && 'bg-accent/40',
        contact.archived_at !== null && 'opacity-60',
      )}
    >
      <td className="px-3 py-2 align-top font-medium">{fullName}</td>
      <td className="px-3 py-2 align-top text-muted-foreground">
        {contact.current_position && <div>{contact.current_position}</div>}
        {contact.current_employer && <div className="text-[12px]">{contact.current_employer}</div>}
        {!contact.current_position && !contact.current_employer && <span>—</span>}
      </td>
      <td className="px-3 py-2 align-top">
        <SourceChip source={contact.source_type} />
      </td>
      {/* Channels cell: anchors call ``stopPropagation`` so clicking
          mailto:/LinkedIn doesn't also open the detail panel. The cell
          itself isn't keyboard-focusable — the anchors inside it are
          the keyboard surface, so the matching ``onKeyDown`` here is a
          no-op that exists only to satisfy a11y/useKeyWithClickEvents. */}
      {/* biome-ignore lint/a11y/useKeyWithClickEvents: cell forwards key events to the anchor children */}
      <td
        className="px-3 py-2 align-top"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2">
          {contact.email_primary && (
            <a
              href={`mailto:${contact.email_primary}`}
              aria-label={`Email ${fullName}`}
              className="inline-flex items-center gap-1 text-[12px] text-muted-foreground hover:text-foreground"
            >
              <Mail className="h-3 w-3" aria-hidden="true" />
              email
            </a>
          )}
          {contact.linkedin_url && (
            <a
              href={contact.linkedin_url}
              target="_blank"
              rel="noopener noreferrer"
              aria-label={`Open LinkedIn for ${fullName}`}
              className="inline-flex items-center gap-1 text-[12px] text-muted-foreground hover:text-foreground"
            >
              <ExternalLink className="h-3 w-3" aria-hidden="true" />
              LinkedIn
            </a>
          )}
          {!contact.email_primary && !contact.linkedin_url && (
            <span className="text-[12px] text-muted-foreground/50">—</span>
          )}
        </div>
      </td>
      <td className="px-3 py-2 align-top font-mono text-[12px] text-muted-foreground">
        {fmtDate(contact.created_at)}
      </td>
    </tr>
  );
}

function SourceChip({ source }: { source: string }) {
  const label = (CONTACT_SOURCE_LABELS as Record<string, string>)[source] ?? source;
  // Tone mapping by source — reuses the same bg-X/15 ring-X/30 vocabulary as
  // the tier and stage badges so the visual hierarchy stays coherent.
  const tone =
    source === 'warm_intro'
      ? 'bg-positive/15 text-positive ring-positive/30'
      : source === 'recruiter_inbound'
        ? 'bg-pending/15 text-pending ring-pending/30'
        : 'bg-muted text-muted-foreground ring-border';
  return (
    <span className={cn('rounded px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset', tone)}>
      {label}
    </span>
  );
}

// ── helpers ─────────────────────────────────────────────────────────────────

function formatFullName(c: ContactListItem): string {
  const first =
    c.preferred_first_name && c.preferred_first_name !== c.first_name
      ? `${c.first_name} (${c.preferred_first_name})`
      : c.first_name;
  return `${first} ${c.last_name}`.trim();
}

function fmtDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
}

// Export internal labels for unit-test reuse without re-importing the dict.
export { CONTACT_SOURCE_LABELS };
export type { ContactSourceType };
