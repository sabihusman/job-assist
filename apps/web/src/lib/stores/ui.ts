'use client';

import { create } from 'zustand';
import { persist } from 'zustand/middleware';

import {
  PIPELINE_BOARD_STAGES,
  type PipelineStage,
  sanitizeColumnOrder,
} from '@/lib/applied/stages';

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
  /**
   * Pipeline kanban column order (feat/pipeline-reorder). Persisted, like
   * sidebarCollapsed — a personal visual preference, NOT a server-side ranking
   * tunable. Always read through `sanitizeColumnOrder` so a stale value can't
   * drop or double a column.
   */
  pipelineColumnOrder: PipelineStage[];
  toggleSidebar: () => void;
  setSidebarCollapsed: (collapsed: boolean) => void;
  openSidebarMobile: () => void;
  closeSidebarMobile: () => void;
  toggleSidebarMobile: () => void;
  openPalette: () => void;
  closePalette: () => void;
  setPaletteOpen: (open: boolean) => void;
  /** Move a column one slot earlier ('up') or later ('down') in the order. */
  movePipelineColumn: (stage: PipelineStage, dir: 'up' | 'down') => void;
  resetPipelineColumnOrder: () => void;
};

export const useUiStore = create<UiState>()(
  persist(
    (set) => ({
      sidebarCollapsed: false,
      sidebarMobileOpen: false,
      paletteOpen: false,
      pipelineColumnOrder: [...PIPELINE_BOARD_STAGES],
      toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
      setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),
      openSidebarMobile: () => set({ sidebarMobileOpen: true }),
      closeSidebarMobile: () => set({ sidebarMobileOpen: false }),
      toggleSidebarMobile: () => set((s) => ({ sidebarMobileOpen: !s.sidebarMobileOpen })),
      openPalette: () => set({ paletteOpen: true }),
      closePalette: () => set({ paletteOpen: false }),
      setPaletteOpen: (open) => set({ paletteOpen: open }),
      movePipelineColumn: (stage, dir) =>
        set((s) => {
          const order = sanitizeColumnOrder(s.pipelineColumnOrder);
          const i = order.indexOf(stage);
          if (i < 0) return {};
          const j = dir === 'up' ? i - 1 : i + 1;
          if (j < 0 || j >= order.length) return {};
          const next = [...order];
          [next[i], next[j]] = [next[j], next[i]];
          return { pipelineColumnOrder: next };
        }),
      resetPipelineColumnOrder: () => set({ pipelineColumnOrder: [...PIPELINE_BOARD_STAGES] }),
    }),
    {
      name: 'job-assist:ui',
      // Persist the desktop sidebar state + the Pipeline column order; palette +
      // mobile drawer are volatile. The mobile drawer should always rehydrate
      // closed — persisting "drawer was open on the last visit" would create a
      // confusing first paint.
      partialize: (state) => ({
        sidebarCollapsed: state.sidebarCollapsed,
        pipelineColumnOrder: state.pipelineColumnOrder,
      }),
    },
  ),
);
