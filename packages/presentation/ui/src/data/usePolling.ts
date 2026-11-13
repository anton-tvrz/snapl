import { useEffect, useRef, useState } from "react";

export interface Polled<T> {
  /** The last answer. Kept when a later request fails. */
  data: T | undefined;
  /** Why the last request failed, or null when it answered. */
  error: Error | null;
}

/**
 * Calls `fn` at once and then every `intervalMs`. A hidden tab asks nothing,
 * and asks at once when it is shown again (spec 011 FR-033). With a null
 * interval it asks once.
 *
 * A change of `key` asks again at once, keeping the last answer on show until
 * the new one arrives, and drops any answer still on its way for the old key.
 * A null key asks nothing.
 */
export function usePolling<T>(fn: () => Promise<T>, intervalMs: number | null, key: string | null = ""): Polled<T> {
  const [state, setState] = useState<Polled<T>>({ data: undefined, error: null });
  const latest = useRef(fn);
  latest.current = fn;

  useEffect(() => {
    if (key === null) return;
    let alive = true;
    let timer: ReturnType<typeof setInterval> | undefined;

    const ask = () => {
      latest.current().then(
        (data) => alive && setState({ data, error: null }),
        (error: unknown) =>
          alive && setState((previous) => ({ data: previous.data, error: error instanceof Error ? error : new Error(String(error)) })),
      );
    };
    const start = () => {
      ask();
      if (intervalMs !== null) timer = setInterval(ask, intervalMs);
    };
    const stop = () => {
      clearInterval(timer);
      timer = undefined;
    };
    const onVisibility = () => {
      stop();
      if (!document.hidden) start();
    };

    if (!document.hidden) start();
    if (intervalMs !== null) document.addEventListener("visibilitychange", onVisibility);
    return () => {
      alive = false;
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [intervalMs, key]);

  return state;
}
