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
  sidebarCollapsed: boolean;
  paletteOpen: boolean;
  toggleSidebar: () => void;
  setSidebarCollapsed: (collapsed: boolean) => void;
  openPalette: () => void;
  closePalette: () => void;
  setPaletteOpen: (open: boolean) => void;
};

export const useUiStore = create<UiState>()(
  persist(
    (set) => ({
      sidebarCollapsed: false,
      paletteOpen: false,
      toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
      setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),
      openPalette: () => set({ paletteOpen: true }),
      closePalette: () => set({ paletteOpen: false }),
      setPaletteOpen: (open) => set({ paletteOpen: open }),
    }),
    {
      name: 'job-assist:ui',
      // Persist only the sidebar state; palette is volatile.
      partialize: (state) => ({ sidebarCollapsed: state.sidebarCollapsed }),
    },
  ),
);
