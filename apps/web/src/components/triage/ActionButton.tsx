'use client';

import { Check, Clock, type LucideIcon, Send, X } from 'lucide-react';

import { cn } from '@/lib/utils';

/**
 * Shared action button used in two contexts:
 *
 *  - `compact` (Triage card action column): h-7, icon + mono hotkey digit,
 *    no label text. Tooltip carries the verb.
 *  - `full`    (Detail panel action bar): full-width, icon + verb +
 *    mono hotkey chip on the right.
 *
 * Variant encodes the semantic color: positive (interested), negative
 * (pass), primary (applied), pending (snooze). Hotkey digits 1-4 are
 * baked in here so call sites don't drift.
 */

export type ActionVariant = 'interested' | 'pass' | 'applied' | 'snooze';

type VariantSpec = {
  label: string;
  hotkey: '1' | '2' | '3' | '4';
  icon: LucideIcon;
  // Compose with `hover:` to keep idle state neutral.
  hoverClasses: string;
};

const VARIANTS: Record<ActionVariant, VariantSpec> = {
  interested: {
    label: 'Interested',
    hotkey: '1',
    icon: Check,
    hoverClasses: 'hover:bg-positive/15 hover:text-positive',
  },
  pass: {
    label: 'Pass',
    hotkey: '2',
    icon: X,
    hoverClasses: 'hover:bg-negative/15 hover:text-negative',
  },
  applied: {
    label: 'Applied',
    hotkey: '3',
    icon: Send,
    hoverClasses: 'hover:bg-primary/15 hover:text-primary',
  },
  snooze: {
    label: 'Snooze',
    hotkey: '4',
    icon: Clock,
    hoverClasses: 'hover:bg-pending/15 hover:text-pending',
  },
};

export function ActionButton({
  variant,
  size,
  onClick,
  disabled,
}: {
  variant: ActionVariant;
  size: 'compact' | 'full';
  onClick: () => void;
  disabled?: boolean;
}) {
  const spec = VARIANTS[variant];
  const Icon = spec.icon;

  if (size === 'compact') {
    return (
      <button
        type="button"
        onClick={onClick}
        disabled={disabled}
        aria-label={`${spec.label} · ${spec.hotkey}`}
        title={`${spec.label} · ${spec.hotkey}`}
        data-action={variant}
        className={cn(
          'inline-flex h-7 items-center gap-1 rounded border border-border bg-surface px-2 text-muted-foreground transition-colors',
          'disabled:opacity-40 disabled:hover:bg-surface disabled:hover:text-muted-foreground',
          spec.hoverClasses,
        )}
      >
        <Icon className="h-3.5 w-3.5" aria-hidden="true" />
        <span className="font-mono text-[11px]">{spec.hotkey}</span>
      </button>
    );
  }

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      data-action={variant}
      className={cn(
        'flex h-9 flex-1 items-center justify-center gap-2 rounded-md border border-border bg-surface text-sm text-foreground/80 transition-colors',
        'disabled:opacity-40 disabled:hover:bg-surface',
        spec.hoverClasses,
      )}
    >
      <Icon className="h-3.5 w-3.5" aria-hidden="true" />
      <span>{spec.label}</span>
      <kbd className="rounded border border-border bg-surface-2 px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
        {spec.hotkey}
      </kbd>
    </button>
  );
}

export const ACTION_VARIANTS = Object.keys(VARIANTS) as ActionVariant[];
