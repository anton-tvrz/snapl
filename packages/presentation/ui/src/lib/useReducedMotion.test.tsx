import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useReducedMotion } from "./useReducedMotion";

afterEach(() => {
  vi.unstubAllGlobals();
});

function stubMedia(initial: boolean) {
  const listeners = new Set<() => void>();
  const media = {
    matches: initial,
    addEventListener: (_: string, listener: () => void) => listeners.add(listener),
    removeEventListener: (_: string, listener: () => void) => listeners.delete(listener),
  };
  vi.stubGlobal("matchMedia", (query: string) => {
    expect(query).toBe("(prefers-reduced-motion: reduce)");
    return media;
  });
  return {
    set(value: boolean) {
      media.matches = value;
      for (const listener of listeners) listener();
    },
    listeners,
  };
}

describe("useReducedMotion", () => {
  it("is false when the system has no preference, or no way to ask", () => {
    stubMedia(false);
    expect(renderHook(() => useReducedMotion()).result.current).toBe(false);

    vi.stubGlobal("matchMedia", undefined);
    expect(renderHook(() => useReducedMotion()).result.current).toBe(false);
  });

  it("follows the system setting as it changes", () => {
    const media = stubMedia(true);
    const { result, unmount } = renderHook(() => useReducedMotion());
    expect(result.current).toBe(true);

    act(() => media.set(false));
    expect(result.current).toBe(false);

    unmount();
    expect(media.listeners.size).toBe(0);
  });
});
