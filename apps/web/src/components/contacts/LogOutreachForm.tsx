'use client';

/**
 * Inline form for logging an outreach message (PR #52).
 *
 * Renders inside ``ContactDetailPanel`` above the timeline. Submit
 * fires ``useOutreachLog`` with the manual ``source`` forced
 * server-side (the wire-shape contract test in
 * ``contacts.test.tsx`` asserts the body never carries ``source``).
 *
 * Validation: ``direction``, ``channel``, ``sent_at`` are required.
 * ``subject`` + ``body`` are optional — operator may log "I sent a
 * LinkedIn note" without pasting the body. Empty strings on those
 * fields become ``null`` on submit (the API treats null and missing
 * equivalently).
 *
 * The "log outreach against archived contact" case is intentionally
 * permitted — archive is a "stop initiating" signal, not "block all
 * interaction" (per Read-First confirmation #3). The form stays
 * enabled even when ``contact.archived_at`` is set.
 */

import { useState } from 'react';
import { toast } from 'sonner';

import { useOutreachLog } from '@/lib/api/contacts';
import {
  MESSAGE_CHANNEL_LABELS,
  MESSAGE_DIRECTION_LABELS,
  type MessageChannel,
  type MessageDirection,
  type OutreachMessageCreate,
} from '@/lib/contacts/types';

type FormState = {
  open: boolean;
  direction: MessageDirection;
  channel: MessageChannel;
  sent_at_local: string; // datetime-local format
  subject: string;
  body: string;
};

function defaultState(): FormState {
  return {
    open: false,
    direction: 'outbound',
    channel: 'linkedin',
    sent_at_local: localNow(),
    subject: '',
    body: '',
  };
}

export function LogOutreachForm({ contactId }: { contactId: string }) {
  const [state, setState] = useState<FormState>(defaultState);
  const log = useOutreachLog();

  const reset = () => setState(defaultState());

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!state.sent_at_local) {
      toast.error('Set a date/time first.');
      return;
    }
    const message: OutreachMessageCreate = {
      direction: state.direction,
      channel: state.channel,
      sent_at: localToIso(state.sent_at_local),
    };
    const subjectTrim = state.subject.trim();
    if (subjectTrim) message.subject = subjectTrim;
    const bodyTrim = state.body.trim();
    if (bodyTrim) message.body = bodyTrim;

    log.mutate(
      { contactId, message },
      {
        onSuccess: () => {
          toast.success('✓ Outreach logged');
          reset();
        },
        onError: (err) => {
          const isMutationError =
            err && typeof err === 'object' && 'detail' in err && 'status' in err;
          const detail = isMutationError
            ? (err as unknown as { detail: string | null }).detail
            : null;
          toast.error(detail ?? 'Log failed.');
        },
      },
    );
  };

  if (!state.open) {
    return (
      <button
        type="button"
        onClick={() => setState((s) => ({ ...s, open: true }))}
        className="self-start rounded-md border border-border bg-surface px-3 py-1 text-[12px] font-medium hover:bg-accent"
        data-testid="log-outreach-open"
      >
        + Log outreach
      </button>
    );
  }

  return (
    <form
      data-testid="log-outreach-form"
      onSubmit={handleSubmit}
      className="flex flex-col gap-3 rounded-md border border-border bg-surface px-3 py-3"
    >
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <RadioGroup
          label="Direction"
          name="direction"
          value={state.direction}
          options={['outbound', 'inbound'] as const}
          labels={MESSAGE_DIRECTION_LABELS}
          onChange={(v) => setState((s) => ({ ...s, direction: v }))}
        />
        <RadioGroup
          label="Channel"
          name="channel"
          value={state.channel}
          options={['email', 'linkedin', 'other'] as const}
          labels={MESSAGE_CHANNEL_LABELS}
          onChange={(v) => setState((s) => ({ ...s, channel: v }))}
        />
      </div>

      <Field label="Sent at">
        <input
          type="datetime-local"
          required
          value={state.sent_at_local}
          onChange={(e) => setState((s) => ({ ...s, sent_at_local: e.target.value }))}
          className={inputCls}
          aria-label="Sent at"
        />
      </Field>

      <Field label="Subject (optional)">
        <input
          type="text"
          value={state.subject}
          onChange={(e) => setState((s) => ({ ...s, subject: e.target.value }))}
          placeholder="e.g. Quick question about your role"
          className={inputCls}
        />
      </Field>

      <Field label="Body (optional)">
        <textarea
          rows={3}
          value={state.body}
          onChange={(e) => setState((s) => ({ ...s, body: e.target.value }))}
          placeholder="Paste the message body if you want a record."
          className={`${inputCls} resize-y`}
        />
      </Field>

      <footer className="flex items-center justify-end gap-2">
        <button
          type="button"
          onClick={reset}
          disabled={log.isPending}
          className="rounded-md border border-border bg-surface px-3 py-1 text-[12px] hover:bg-accent disabled:opacity-50"
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={log.isPending}
          className="rounded-md border border-border bg-accent px-3 py-1 text-[12px] font-medium disabled:opacity-50"
        >
          {log.isPending ? 'Logging…' : 'Log'}
        </button>
      </footer>
    </form>
  );
}

function RadioGroup<T extends string>({
  label,
  name,
  value,
  options,
  labels,
  onChange,
}: {
  label: string;
  name: string;
  value: T;
  options: readonly T[];
  labels: Record<T, string>;
  onChange: (next: T) => void;
}) {
  return (
    <fieldset className="flex flex-col gap-1">
      <legend className="font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
        {label}
      </legend>
      <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label={label}>
        {options.map((opt) => {
          const checked = opt === value;
          return (
            <label
              key={opt}
              className={`cursor-pointer rounded px-2 py-1 text-[12px] ring-1 ring-inset ${
                checked
                  ? 'bg-accent text-foreground ring-border-strong'
                  : 'bg-surface text-muted-foreground ring-border hover:text-foreground'
              }`}
            >
              <input
                type="radio"
                name={name}
                value={opt}
                checked={checked}
                onChange={() => onChange(opt)}
                className="sr-only"
              />
              {labels[opt]}
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    // biome-ignore lint/a11y/noLabelWithoutControl: input is supplied via children prop
    <label className="flex flex-col gap-1 text-[12px]">
      <span className="font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      {children}
    </label>
  );
}

const inputCls =
  'w-full rounded bg-surface px-2 py-1 text-[13px] ring-1 ring-inset ring-border text-foreground placeholder:text-muted-foreground/60 focus:outline-none focus:ring-2 focus:ring-ring';

// ── time helpers ─────────────────────────────────────────────────────────────

/** Current local time formatted for ``<input type="datetime-local">``. */
function localNow(): string {
  const d = new Date();
  // datetime-local expects "YYYY-MM-DDTHH:MM" in the operator's local
  // tz. Strip the timezone suffix from toISOString by going through
  // a tz offset adjustment.
  const tzOffsetMs = d.getTimezoneOffset() * 60 * 1000;
  const localIso = new Date(d.getTime() - tzOffsetMs).toISOString();
  return localIso.slice(0, 16);
}

/** datetime-local string → full ISO 8601 with the operator's tz preserved. */
function localToIso(local: string): string {
  // ``new Date("2026-06-03T14:30")`` parses as local time by default.
  // Round-trip through Date so we emit a tz-aware ISO the API expects.
  return new Date(local).toISOString();
}
