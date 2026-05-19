'use client';

import { useEffect } from 'react';

/**
 * Listen for a single keystroke (with optional modifiers) on `window` and
 * fire the handler. Used for the global `⌘K` / `Ctrl+K` palette open.
 *
 * The handler is invoked even when focus is inside an input — that
 * matches the Lovable build's behavior for `⌘K` specifically. For other
 * shortcuts (J/K Triage nav, the chord G-X) we'll want to gate on
 * `event.target` not being a contenteditable / input; do that at the
 * call site rather than here.
 */
type Mods = { meta?: boolean; ctrl?: boolean; shift?: boolean; alt?: boolean };

export function useGlobalShortcut(key: string, mods: Mods, handler: (e: KeyboardEvent) => void) {
  useEffect(() => {
    const listener = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() !== key.toLowerCase()) return;
      if (mods.meta !== undefined && e.metaKey !== mods.meta) return;
      if (mods.ctrl !== undefined && e.ctrlKey !== mods.ctrl) return;
      if (mods.shift !== undefined && e.shiftKey !== mods.shift) return;
      if (mods.alt !== undefined && e.altKey !== mods.alt) return;
      handler(e);
    };
    window.addEventListener('keydown', listener);
    return () => window.removeEventListener('keydown', listener);
  }, [key, mods.meta, mods.ctrl, mods.shift, mods.alt, handler]);
}
