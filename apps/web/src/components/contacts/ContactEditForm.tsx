'use client';

/**
 * Inline edit form for a single contact (PR #52).
 *
 * Editable fields: notes, opt-in toggle + topics, current_position,
 * current_employer, email_primary, email_secondary, linkedin_url,
 * phone. Saves call ``PATCH /contacts/{id}`` with the diff.
 *
 * Immutable fields (first_name, last_name, source_type) are
 * displayed read-only at the panel header — they're NOT in this
 * form because the API rejects them via ``extra='forbid'``.
 */

import { useState } from 'react';
import { toast } from 'sonner';

import { useContactUpdate } from '@/lib/api/contacts';
import type { ContactDetail, ContactUpdate } from '@/lib/contacts/types';

/** Compute the diff between the current edits and the original detail.
 *
 * Returns ONLY fields that changed. ``undefined`` means "key absent
 * from the PATCH body" (server leaves the field alone); ``null``
 * means "explicit clear". The form converts empty strings to ``null``
 * before diffing so an operator clearing a notes field actually
 * clears it server-side.
 */
function diffPatch(original: ContactDetail, edits: Partial<ContactDetail>): ContactUpdate {
  const out: Record<string, unknown> = {};
  const keys: (keyof ContactUpdate)[] = [
    'preferred_first_name',
    'email_primary',
    'email_secondary',
    'linkedin_url',
    'phone',
    'current_employer',
    'current_position',
    'notes',
    'contact_opt_in',
    'contact_opt_in_topics',
  ];
  for (const k of keys) {
    if (!(k in edits)) continue;
    const next = edits[k as keyof ContactDetail];
    if (next === original[k as keyof ContactDetail]) continue;
    out[k] = next;
  }
  return out as ContactUpdate;
}

function emptyToNull(v: string): string | null {
  return v.trim() ? v.trim() : null;
}

export function ContactEditForm({ contact }: { contact: ContactDetail }) {
  const [edits, setEdits] = useState<Partial<ContactDetail>>({});
  const update = useContactUpdate();

  const value = <K extends keyof ContactDetail>(key: K): ContactDetail[K] =>
    (key in edits ? edits[key] : contact[key]) as ContactDetail[K];

  const patch = diffPatch(contact, edits);
  const isDirty = Object.keys(patch).length > 0;

  const handleSave = () => {
    if (!isDirty) return;
    update.mutate(
      { contactId: contact.id, patch },
      {
        onSuccess: () => {
          toast.success('✓ Saved');
          setEdits({});
        },
        onError: (err) => {
          const isMutationError =
            err && typeof err === 'object' && 'detail' in err && 'status' in err;
          const detail = isMutationError
            ? (err as unknown as { detail: string | null }).detail
            : null;
          toast.error(detail ?? 'Save failed.');
        },
      },
    );
  };

  const handleReset = () => setEdits({});

  return (
    <form
      data-testid="contact-edit-form"
      onSubmit={(e) => {
        e.preventDefault();
        handleSave();
      }}
      className="flex flex-col gap-3"
    >
      <Field label="Current position">
        <input
          type="text"
          value={(value('current_position') as string) ?? ''}
          onChange={(e) =>
            setEdits((s) => ({ ...s, current_position: emptyToNull(e.target.value) }))
          }
          className={inputCls}
        />
      </Field>

      <Field label="Current employer">
        <input
          type="text"
          value={(value('current_employer') as string) ?? ''}
          onChange={(e) =>
            setEdits((s) => ({ ...s, current_employer: emptyToNull(e.target.value) }))
          }
          className={inputCls}
        />
      </Field>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="Email (primary)">
          <input
            type="email"
            value={(value('email_primary') as string) ?? ''}
            onChange={(e) =>
              setEdits((s) => ({ ...s, email_primary: emptyToNull(e.target.value) }))
            }
            className={inputCls}
          />
        </Field>
        <Field label="Email (secondary)">
          <input
            type="email"
            value={(value('email_secondary') as string) ?? ''}
            onChange={(e) =>
              setEdits((s) => ({ ...s, email_secondary: emptyToNull(e.target.value) }))
            }
            className={inputCls}
          />
        </Field>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="LinkedIn URL">
          <input
            type="url"
            value={(value('linkedin_url') as string) ?? ''}
            onChange={(e) =>
              setEdits((s) => ({ ...s, linkedin_url: emptyToNull(e.target.value) }))
            }
            className={inputCls}
          />
        </Field>
        <Field label="Phone">
          <input
            type="tel"
            value={(value('phone') as string) ?? ''}
            onChange={(e) => setEdits((s) => ({ ...s, phone: emptyToNull(e.target.value) }))}
            className={inputCls}
          />
        </Field>
      </div>

      <Field label="Notes">
        <textarea
          value={(value('notes') as string) ?? ''}
          onChange={(e) => setEdits((s) => ({ ...s, notes: emptyToNull(e.target.value) }))}
          rows={3}
          className={`${inputCls} resize-y`}
        />
      </Field>

      <label className="flex items-center gap-2 text-[12px]">
        <input
          type="checkbox"
          checked={value('contact_opt_in') as boolean}
          onChange={(e) => setEdits((s) => ({ ...s, contact_opt_in: e.target.checked }))}
        />
        Opt-in to outreach
      </label>

      <footer className="flex items-center justify-end gap-2 pt-2">
        {isDirty && (
          <button
            type="button"
            onClick={handleReset}
            disabled={update.isPending}
            className="rounded-md border border-border bg-surface px-3 py-1 text-[12px] hover:bg-accent disabled:opacity-50"
          >
            Cancel
          </button>
        )}
        <button
          type="submit"
          disabled={!isDirty || update.isPending}
          aria-label="Save contact changes"
          className="rounded-md border border-border bg-accent px-3 py-1 text-[12px] font-medium disabled:opacity-50"
        >
          {update.isPending ? 'Saving…' : 'Save'}
        </button>
      </footer>
    </form>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  // Biome's noLabelWithoutControl can't statically verify the input
  // arrives via children. The wrapped <label> + <input> pattern is
  // standard HTML — the label associates by ancestor relationship.
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
