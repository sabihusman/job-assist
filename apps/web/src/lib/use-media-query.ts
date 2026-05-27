'use client';

import { useEffect, useState } from 'react';

/**
 * Subscribe to a CSS media query (UX overhaul PR 1).
 *
 * SSR-safe: the initial state is ``false`` (mobile-first assumption),
 * then ``useEffect`` syncs to the real value post-hydration. This is
 * an interaction-only concern — the DetailPanel mobile Sheet only opens
 * after the operator clicks a card, by which time hydration has long
 * completed, so the SSR default never produces a visible flash.
 *
 * Why a hook, not a CSS-only gate
 * ───────────────────────────────
 * Radix Dialog marks every sibling of its open content with
 * ``aria-hidden="true"`` to enforce the modal trap. CSS-only
 * ``lg:hidden`` hides the Dialog's visible body but doesn't stop
 * Radix from running. So opening a Sheet at lg+ (where the desktop
 * surface is the actual UI) silently makes the entire FilterRow,
 * sidebar, etc. inaccessible — that's a Playwright E2E test failure
 * waiting to happen and a screen-reader regression at every viewport
 * that allegedly "shouldn't" trigger the Sheet.
 *
 * Gating Sheet ``open`` by this hook keeps Radix fully out of the
 * DOM at lg+.
 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(false);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const mq = window.matchMedia(query);
    const update = () => setMatches(mq.matches);
    update();
    // ``addEventListener('change', ...)`` is the modern API; Safari
    // 13 and earlier needed ``addListener``. Modern Vercel deploys
    // ship to evergreen browsers so the modern path is fine.
    mq.addEventListener('change', update);
    return () => mq.removeEventListener('change', update);
  }, [query]);

  return matches;
}

/**
 * Tailwind's ``lg`` breakpoint = 1024px. ``useIsLgUp()`` returns true
 * when the viewport is wide enough that the desktop-inplace surface
 * (the Triage DetailPanel aside) is the visible one.
 */
export function useIsLgUp(): boolean {
  return useMediaQuery('(min-width: 1024px)');
}
