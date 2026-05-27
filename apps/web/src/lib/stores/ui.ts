'use client';

import { create } from 'zustand';
import { persist } from 'zustand/middleware';

/**
 * UI-shell state: things the chrome cares about but pages don't.
 *
 * `sidebarCollapsed` persists across reloads (per UI_SPEC.md — the
 * collapsed state survives refresh in the Lovable build). Command
 * palette `paletteOpen` is volatile — never persisted; ⌘K should
 * always open a fresh palette.
 *
 * Filter state lives in URL search params, NOT here — the operator
 * needs to share filtered Triage views as URLs.
 */
type UiState = {
  /** Desktop: collapsed = narrow icon-rail. Persisted. */
  sidebarCollapsed: boolean;
  /**
   * Mobile (<md): off-canvas drawer open/closed. Volatile — should
   * always start closed on page load. The hamburger in Banner flips
   * it; the backdrop / a route change closes it. Desktop ignores this.
   * (UX overhaul PR 1.)
   */
  sidebarMobileOpen: boolean;
  paletteOpen: boolean;
  toggleSidebar: () => void;
  setSidebarCollapsed: (collapsed: boolean) => void;
  openSidebarMobile: () => void;
  closeSidebarMobile: () => void;
  toggleSidebarMobile: () => void;
  openPalette: () => void;
  closePalette: () => void;
  setPaletteOpen: (open: boolean) => void;
};

export const useUiStore = create<UiState>()(
  persist(
    (set) => ({
      sidebarCollapsed: false,
      sidebarMobileOpen: false,
      paletteOpen: false,
      toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
      setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),
      openSidebarMobile: () => set({ sidebarMobileOpen: true }),
      closeSidebarMobile: () => set({ sidebarMobileOpen: false }),
      toggleSidebarMobile: () => set((s) => ({ sidebarMobileOpen: !s.sidebarMobileOpen })),
      openPalette: () => set({ paletteOpen: true }),
      closePalette: () => set({ paletteOpen: false }),
      setPaletteOpen: (open) => set({ paletteOpen: open }),
    }),
    {
      name: 'job-assist:ui',
      // Persist only the desktop sidebar state; palette + mobile drawer are
      // volatile. The mobile drawer should always rehydrate closed —
      // persisting "drawer was open on the last visit" would create a
      // confusing first paint.
      partialize: (state) => ({ sidebarCollapsed: state.sidebarCollapsed }),
    },
  ),
);
