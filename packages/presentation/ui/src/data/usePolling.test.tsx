import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { usePolling } from "./usePolling";

function setHidden(hidden: boolean) {
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
  document.dispatchEvent(new Event("visibilitychange"));
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  setHidden(false);
});

afterEach(() => {
  vi.useRealTimers();
  setHidden(false);
});

describe("usePolling", () => {
  it("asks at once, then at its interval", async () => {
    const fn = vi.fn(async () => "answer");
    const { result } = renderHook(() => usePolling(fn, 5000));
    await flush();

    expect(fn).toHaveBeenCalledTimes(1);
    expect(result.current.data).toBe("answer");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(fn).toHaveBeenCalledTimes(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(fn).toHaveBeenCalledTimes(4);
  });

  it("stops while the tab is hidden and asks at once when it is shown again", async () => {
    const fn = vi.fn(async () => "answer");
    renderHook(() => usePolling(fn, 5000));
    await flush();

    act(() => setHidden(true));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60000);
    });
    expect(fn).toHaveBeenCalledTimes(1);

    act(() => setHidden(false));
    await flush();
    expect(fn).toHaveBeenCalledTimes(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(fn).toHaveBeenCalledTimes(3);
  });

  it("keeps the last answer and reports the error when a request fails", async () => {
    const fn = vi
      .fn<() => Promise<string>>()
      .mockResolvedValueOnce("first")
      .mockRejectedValueOnce(new Error("the dashboard server did not answer"))
      .mockResolvedValue("later");
    const { result } = renderHook(() => usePolling(fn, 5000));
    await flush();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });

    expect(result.current.data).toBe("first");
    expect(result.current.error?.message).toBe("the dashboard server did not answer");
  });

  it("clears the error on the next answer", async () => {
    const fn = vi.fn<() => Promise<string>>().mockRejectedValueOnce(new Error("down")).mockResolvedValue("back");
    const { result } = renderHook(() => usePolling(fn, 5000));
    await flush();
    expect(result.current.error?.message).toBe("down");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });

    expect(result.current.error).toBeNull();
    expect(result.current.data).toBe("back");
  });

  it("asks once and never again when the interval is null", async () => {
    const fn = vi.fn(async () => "once");
    renderHook(() => usePolling(fn, null));
    await flush();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60000);
    });
    act(() => setHidden(true));
    act(() => setHidden(false));
    await flush();

    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("stops when the component goes away", async () => {
    const fn = vi.fn(async () => "answer");
    const { unmount } = renderHook(() => usePolling(fn, 5000));
    await flush();

    unmount();
    await vi.advanceTimersByTimeAsync(30000);

    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("ignores an answer that arrives after the component went away", async () => {
    let resolve: (value: string) => void = () => {};
    const fn = vi.fn(() => new Promise<string>((done) => (resolve = done)));
    const { result, unmount } = renderHook(() => usePolling(fn, 5000));

    unmount();
    resolve("late");
    await Promise.resolve();

    expect(result.current.data).toBeUndefined();
  });

  it("asks again at once when its key changes, and drops the answer for the old key", async () => {
    let resolveOld: (value: string) => void = () => {};
    const fn = vi
      .fn<(key: string) => Promise<string>>()
      .mockImplementationOnce(() => new Promise<string>((done) => (resolveOld = done)))
      .mockImplementation(async (key) => `answer for ${key}`);
    const { result, rerender } = renderHook(({ key }) => usePolling(() => fn(key), 60000, key), {
      initialProps: { key: "a" },
    });

    rerender({ key: "b" });
    await flush();
    resolveOld("answer for a");
    await flush();

    expect(fn).toHaveBeenLastCalledWith("b");
    expect(result.current.data).toBe("answer for b");
  });

  it("keeps the last answer while a new key is asked for", async () => {
    let resolveB: (value: string) => void = () => {};
    const fn = vi
      .fn<(key: string) => Promise<string>>()
      .mockImplementationOnce(async () => "answer for a")
      .mockImplementationOnce(() => new Promise<string>((done) => (resolveB = done)))
      .mockImplementation(async (key) => `answer for ${key}`);
    const { result, rerender } = renderHook(({ key }) => usePolling(() => fn(key), 60000, key), {
      initialProps: { key: "a" },
    });
    await flush();

    rerender({ key: "b" });
    await flush();
    expect(result.current.data).toBe("answer for a");

    resolveB("answer for b");
    await flush();
    expect(result.current.data).toBe("answer for b");
  });

  it("asks nothing while its key is null", async () => {
    const fn = vi.fn(async () => "answer");
    const { rerender } = renderHook(({ key }) => usePolling(fn, 60000, key), {
      initialProps: { key: null as string | null },
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(120000);
    });
    expect(fn).not.toHaveBeenCalled();

    rerender({ key: "now" });
    await flush();
    expect(fn).toHaveBeenCalledTimes(1);
  });
});
