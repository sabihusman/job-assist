'use client';

import { useEffect } from 'react';

/**
 * Triage page keyboard shortcuts. Lives on the page (not the list)
 * because the same `selectedIndex` drives the right detail panel.
 *
 *   J            move selection down
 *   K            move selection up
 *   1 / 2 / 3 / 4  fire action 1-4 on selected card
 *   Esc          clear selection (close detail panel)
 *
 * The reason picker (1-9 + esc) has its own listener inside
 * `ReasonPicker`. When the picker is open, this hook is paused via
 * `enabled=false` so the two don't fight over keystrokes.
 *
 * Inputs / contenteditable are ignored so users can type in palette
 * search etc. without triggering nav.
 */

type Handler = (e: KeyboardEvent) => void;

function isEditable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable;
}

export type TriageKeyboardCallbacks = {
  onNext: () => void;
  onPrev: () => void;
  onAction1: () => void;
  onAction2: () => void;
  onAction3: () => void;
  onAction4: () => void;
  onEscape: () => void;
};

export function useTriageKeyboard(cb: TriageKeyboardCallbacks, enabled: boolean) {
  useEffect(() => {
    if (!enabled) return;
    const dispatch: Record<string, Handler> = {
      j: () => cb.onNext(),
      J: () => cb.onNext(),
      k: () => cb.onPrev(),
      K: () => cb.onPrev(),
      '1': () => cb.onAction1(),
      '2': () => cb.onAction2(),
      '3': () => cb.onAction3(),
      '4': () => cb.onAction4(),
      Escape: () => cb.onEscape(),
    };
    const listener = (e: KeyboardEvent) => {
      if (isEditable(e.target)) return;
      // Ignore modifier-combos (⌘K etc. belong to the palette).
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const handler = dispatch[e.key];
      if (handler) {
        e.preventDefault();
        handler(e);
      }
    };
    window.addEventListener('keydown', listener);
    return () => window.removeEventListener('keydown', listener);
  }, [enabled, cb]);
}
