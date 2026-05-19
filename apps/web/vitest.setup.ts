import '@testing-library/jest-dom/vitest';

/**
 * jsdom doesn't ship `matchMedia`, `ResizeObserver`, or
 * `IntersectionObserver`, but next-themes (matchMedia) and cmdk
 * (ResizeObserver) both reach for them during render. Stub them as
 * no-ops so the chrome tests can mount their providers.
 */
if (typeof window !== 'undefined') {
  if (!window.matchMedia) {
    window.matchMedia = (query: string) =>
      ({
        matches: false,
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }) as MediaQueryList;
  }

  if (!('ResizeObserver' in window)) {
    class ResizeObserverStub {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    (window as unknown as { ResizeObserver: typeof ResizeObserverStub }).ResizeObserver =
      ResizeObserverStub;
  }

  // cmdk calls `.scrollIntoView()` on the selected item as the user
  // arrows through the list. jsdom doesn't implement it; stub as a no-op.
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = function () {};
  }

  if (!('IntersectionObserver' in window)) {
    class IntersectionObserverStub {
      readonly root = null;
      readonly rootMargin = '';
      readonly thresholds = [];
      observe() {}
      unobserve() {}
      disconnect() {}
      takeRecords() {
        return [];
      }
    }
    (
      window as unknown as { IntersectionObserver: typeof IntersectionObserverStub }
    ).IntersectionObserver = IntersectionObserverStub;
  }
}
