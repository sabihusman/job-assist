'use client';

import { X } from 'lucide-react';
import { type KeyboardEvent, useState } from 'react';

import { cn } from '@/lib/utils';

/**
 * Chip-list input. Used by Settings → Profile for `role_keywords` and
 * `geo_whitelist`.
 *
 * Commits on Enter or comma; trims whitespace and dedupes (case-
 * sensitive, preserving order) before adding. Existing tags get an ×
 * remove button. The component is fully controlled — pass an array
 * and an `onChange` that writes the new array somewhere (typically a
 * react-hook-form field).
 */
export function TagInput({
  value,
  onChange,
  placeholder,
  inputAriaLabel,
  className,
}: {
  value: readonly string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  inputAriaLabel?: string;
  className?: string;
}) {
  const [draft, setDraft] = useState('');

  const commit = (raw: string) => {
    const trimmed = raw.trim();
    if (!trimmed) return;
    if (value.includes(trimmed)) {
      setDraft('');
      return;
    }
    onChange([...value, trimmed]);
    setDraft('');
  };

  const remove = (idx: number) => {
    onChange(value.filter((_, i) => i !== idx));
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      commit(draft);
    } else if (e.key === 'Backspace' && draft === '' && value.length > 0) {
      // Backspace on empty input deletes the last chip — common pattern.
      onChange(value.slice(0, -1));
    }
  };

  return (
    <div
      className={cn(
        'flex flex-wrap items-center gap-1.5 rounded-md border border-border bg-input px-2 py-1.5',
        'focus-within:border-border-strong',
        className,
      )}
    >
      {value.map((tag, i) => (
        <span
          // Index in the key is fine — list isn't reordered, only appended/spliced.
          // biome-ignore lint/suspicious/noArrayIndexKey: stable position keying
          key={`${tag}-${i}`}
          className="inline-flex items-center gap-1 rounded bg-surface-2 px-1.5 py-0.5 text-[12px]"
        >
          {tag}
          <button
            type="button"
            onClick={() => remove(i)}
            aria-label={`Remove ${tag}`}
            className="text-muted-foreground hover:text-foreground"
          >
            <X className="h-3 w-3" />
          </button>
        </span>
      ))}
      <input
        type="text"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder={placeholder}
        aria-label={inputAriaLabel}
        className="flex-1 min-w-[120px] bg-transparent text-[13px] outline-none placeholder:text-muted-foreground"
      />
    </div>
  );
}
