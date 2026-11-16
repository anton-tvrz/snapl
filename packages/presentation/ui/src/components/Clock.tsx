import { useEffect, useState } from "react";

import { useReducedMotion } from "../lib/useReducedMotion";
import { utcText } from "../lib/time";

interface Props {
  /** The time the page is showing, from the server. Undefined until it is known. */
  at: Date | undefined;
  /** True in live mode at the present. An archive, a recording and a past time stand still. */
  running: boolean;
}

/**
 * The time the page is showing (spec 014 FR-030, research R8). It starts from
 * the server's time and counts only the seconds that have passed since: the
 * browser's clock never dates anything. With reduced motion it shows minutes,
 * so nothing changes every second.
 */
export function Clock({ at, running }: Props) {
  const reduced = useReducedMotion();
  const base = at?.getTime();
  const [elapsed, setElapsed] = useState(0);
  const ticking = running && !reduced && base !== undefined;

  useEffect(() => {
    setElapsed(0);
    if (!ticking) return;
    const started = Date.now();
    const timer = setInterval(() => setElapsed(Date.now() - started), 1000);
    return () => clearInterval(timer);
  }, [base, ticking]);

  if (base === undefined) return null;
  const shown = new Date(Math.floor((base + (ticking ? elapsed : 0)) / 1000) * 1000);
  const text = utcText(shown);
  return (
    <time
      dateTime={shown.toISOString()}
      aria-label={`The time shown: ${text}`}
      data-running={ticking}
      className="font-mono text-title font-semibold tabular-nums"
    >
      {reduced ? `${text.slice(11, 16)} UTC` : text.slice(11)}
    </time>
  );
}
