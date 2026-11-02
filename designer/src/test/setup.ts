// vitest setup: jest-dom matchers for component tests (PROC-TDD, SPA layer).
import { beforeEach } from "vitest";
import "@testing-library/jest-dom/vitest";

import { clear as clearDataCache } from "../data-cache.ts";

// PERF-CLIENT-REVISION — start every test with a COLD data cache.
//
// The cache is process-global by design: one tab, one user, one cache, invalidated by the server's
// revision rather than by a clock. In a test file that is one cache shared by every case, so a case
// that stubs `fetch` differently reads the previous case's entry and never reaches the branch it was
// written for (`fetchDefinitions`' error case reads the success case's index, for one). It belongs HERE
// rather than in each file's `beforeEach`: a per-file reset is a thing every future test file has to
// remember, and forgetting it produces a passing test that asserts the wrong thing.
beforeEach(() => clearDataCache());

// jsdom implements no layout, so it ships no `scrollIntoView`. Several surfaces keep the thing they just
// selected in view — the command palette's keyboard cursor, the Specification's jump-to-criterion — and
// without this the ABSENCE of a browser API fails tests about behaviour that has nothing to do with
// scrolling. Stubbed here, once, rather than guarded at each call site: the product code should not carry
// a branch for a test environment (design simplicity — one implementation path).
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = function scrollIntoView() { /* no layout in jsdom */ };
}

// Same reasoning, same rule: jsdom ships no `matchMedia`, so a component that asks whether the reader
// prefers reduced motion (the Product Tour's reveal-on-scroll) throws on mount — a test about navigation
// failing on an absent media-query API. Reports "no preference", the browser default: the reveal
// animation is then the path under test, which is the one a reader gets.
if (!window.matchMedia) {
  window.matchMedia = (query: string) => ({
    media: query, matches: false, onchange: null,
    addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {}, dispatchEvent: () => false,
  }) as MediaQueryList;
}

// And its partner: `IntersectionObserver` is the reveal's other half, and jsdom has no viewport to
// intersect with. Observes nothing and fires nothing — an element that never reveals is exactly what a
// zero-height layout would produce, so nothing here pretends the animation ran.
if (!window.IntersectionObserver) {
  class NoopIntersectionObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
    takeRecords() { return []; }
  }
  const Noop = NoopIntersectionObserver as unknown as typeof IntersectionObserver;
  window.IntersectionObserver = Noop;
  globalThis.IntersectionObserver = Noop;
}
