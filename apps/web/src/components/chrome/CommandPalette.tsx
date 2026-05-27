'use client';

import { useRouter } from 'next/navigation';
import { useCallback } from 'react';
import { toast } from 'sonner';

import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command';
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';
import { useGlobalShortcut } from '@/lib/keyboard/useGlobalShortcut';
import { useUiStore } from '@/lib/stores/ui';

/**
 * ⌘K / Ctrl+K command palette.
 *
 * Per UI_SPEC.md:
 * - Default items: 6 "Go to {page}" entries + 5 action stubs
 * - Outreach intentionally absent — stripped for v1
 * - Substring filter (case-insensitive) — cmdk's default
 * - "G X" chord hints are display-only in #32a (chord shortcuts
 *   themselves don't fire; the useChordShortcut hook is stubbed)
 * - Footer key-chips ↑↓ navigate · ↵ select · esc close · job-assist
 *
 * The action stubs (discover-ats / Gmail backfill / etc.) fire a
 * sonner toast "Coming in PR #32c" and close the palette. PR #32c
 * wires Settings → Manual job triggers properly.
 */

type NavCommand = {
  type: 'nav';
  label: string;
  // PR #72: added /passed, /rejected, /contacts so the palette covers
  // every entry in the Sidebar nav. No shortcut hints on the new three
  // — letters P, C, S are already claimed (Pipeline/Companies/Settings)
  // and arbitrary single letters would obscure rather than help.
  href:
    | '/'
    | '/applied'
    | '/passed'
    | '/rejected'
    | '/pipeline'
    | '/companies'
    | '/contacts'
    | '/stats'
    | '/settings';
  shortcut?: string;
};

type StubCommand = {
  type: 'stub';
  label: string;
  tag: 'job' | 'system' | 'data';
};

type PaletteCommand = NavCommand | StubCommand;

const COMMANDS: readonly PaletteCommand[] = [
  { type: 'nav', label: 'Go to Triage', href: '/', shortcut: 'G T' },
  { type: 'nav', label: 'Go to Applied', href: '/applied', shortcut: 'G A' },
  // PR #72: Passed/Rejected/Contacts slot in their Sidebar order. No
  // shortcut hints (see NavCommand type comment for the rationale).
  { type: 'nav', label: 'Go to Passed', href: '/passed' },
  { type: 'nav', label: 'Go to Rejected', href: '/rejected' },
  { type: 'nav', label: 'Go to Pipeline', href: '/pipeline', shortcut: 'G P' },
  { type: 'nav', label: 'Go to Companies', href: '/companies', shortcut: 'G C' },
  { type: 'nav', label: 'Go to Contacts', href: '/contacts' },
  { type: 'nav', label: 'Go to Stats', href: '/stats', shortcut: 'G S' },
  // Settings intentionally has no shortcut hint — matches UI_SPEC.md.
  { type: 'nav', label: 'Go to Settings', href: '/settings' },
  { type: 'stub', label: 'Run discover-ats', tag: 'job' },
  { type: 'stub', label: 'Run Gmail backfill', tag: 'job' },
  { type: 'stub', label: 'Run Greenhouse ingestion', tag: 'job' },
  { type: 'stub', label: 'Rotate API keys', tag: 'system' },
  { type: 'stub', label: 'Export postings as CSV', tag: 'data' },
];

export function CommandPalette() {
  const router = useRouter();
  const paletteOpen = useUiStore((s) => s.paletteOpen);
  const openPalette = useUiStore((s) => s.openPalette);
  const closePalette = useUiStore((s) => s.closePalette);
  const setPaletteOpen = useUiStore((s) => s.setPaletteOpen);

  // ⌘K and Ctrl+K both open. The two listeners are mutually exclusive
  // by modifier so we don't double-fire on mac with the wrong key.
  useGlobalShortcut(
    'k',
    { meta: true },
    useCallback(
      (e) => {
        e.preventDefault();
        openPalette();
      },
      [openPalette],
    ),
  );
  useGlobalShortcut(
    'k',
    { ctrl: true, meta: false },
    useCallback(
      (e) => {
        e.preventDefault();
        openPalette();
      },
      [openPalette],
    ),
  );

  const handleSelect = (cmd: PaletteCommand) => {
    closePalette();
    if (cmd.type === 'nav') {
      router.push(cmd.href);
    } else {
      toast(`${cmd.label} — coming in PR #32c`);
    }
  };

  return (
    <Dialog open={paletteOpen} onOpenChange={setPaletteOpen}>
      <DialogContent className="overflow-hidden p-0" hideCloseButton>
        <DialogTitle className="sr-only">Command palette</DialogTitle>
        <Command label="Command palette">
          <CommandInput placeholder="Search commands, jump to page…" />
          <CommandList>
            <CommandEmpty>No matches</CommandEmpty>
            <CommandGroup>
              {COMMANDS.map((cmd) => (
                <CommandItem key={cmd.label} value={cmd.label} onSelect={() => handleSelect(cmd)}>
                  <span aria-hidden="true" className="text-muted-foreground">
                    →
                  </span>
                  <span className="flex-1">{cmd.label}</span>
                  {cmd.type === 'nav' && cmd.shortcut && (
                    <span className="flex items-center gap-1 font-mono text-[10px] text-muted-foreground">
                      {cmd.shortcut.split(' ').map((k) => (
                        <kbd
                          key={k}
                          className="rounded border border-border bg-surface-2 px-1.5 py-0.5"
                        >
                          {k}
                        </kbd>
                      ))}
                    </span>
                  )}
                  {cmd.type === 'stub' && (
                    <span className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
                      {cmd.tag}
                    </span>
                  )}
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
          {/* Footer */}
          <div className="flex items-center justify-between border-t border-border px-3 py-2 font-mono text-[11px] text-muted-foreground">
            <div className="flex items-center gap-3">
              <span className="flex items-center gap-1">
                <kbd className="rounded border border-border bg-surface-2 px-1 py-0.5">↑↓</kbd>{' '}
                navigate
              </span>
              <span className="flex items-center gap-1">
                <kbd className="rounded border border-border bg-surface-2 px-1 py-0.5">↵</kbd>{' '}
                select
              </span>
              <span className="flex items-center gap-1">
                <kbd className="rounded border border-border bg-surface-2 px-1 py-0.5">esc</kbd>{' '}
                close
              </span>
            </div>
            <span>job-assist</span>
          </div>
        </Command>
      </DialogContent>
    </Dialog>
  );
}
