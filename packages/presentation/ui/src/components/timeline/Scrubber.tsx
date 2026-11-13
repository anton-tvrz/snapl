import { type KeyboardEvent, type PointerEvent, useState } from "react";

import { nextStop, previousStop, type Range } from "../../lib/lanes";
import { utcText } from "../../lib/time";
import { NAME_COLUMN } from "./Lanes";

interface Props {
  domain: Range;
  /** One stop per scan (lib/lanes scanStops). The arrow keys move between them. */
  stops: readonly Date[];
  /** The "as of" time. Undefined is now. */
  value?: Date;
  onChange: (value?: Date) => void;
}

/**
 * The "as of" control (spec 011 FR-023). The pointer drags it and sets the time
 * where it is let go; the arrow keys step one scan; Home goes to the range's
 * start and End back to now. Letting go at the right end is now, too.
 */
export function Scrubber({ domain, stops, value, onChange }: Props) {
  const [dragging, setDragging] = useState<Date | undefined>();
  const start = domain.start.getTime();
  const span = domain.end.getTime() - start;
  const shown = dragging ?? value;
  const left = shown ? Math.min(100, Math.max(0, ((shown.getTime() - start) / span) * 100)) : 100;

  const timeAt = (event: PointerEvent<HTMLDivElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    const fraction = Math.min(1, Math.max(0, (event.clientX - box.left) / box.width));
    return new Date(start + fraction * span);
  };

  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    event.currentTarget.setPointerCapture?.(event.pointerId);
    setDragging(timeAt(event));
  };
  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (dragging) setDragging(timeAt(event));
  };
  const onPointerUp = (event: PointerEvent<HTMLDivElement>) => {
    if (!dragging) return;
    const t = timeAt(event);
    setDragging(undefined);
    onChange(t >= domain.end ? undefined : t);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const moves: Record<string, () => Date | undefined> = {
      ArrowLeft: () => previousStop(stops, value),
      ArrowDown: () => previousStop(stops, value),
      ArrowRight: () => nextStop(stops, value),
      ArrowUp: () => nextStop(stops, value),
      Home: () => domain.start,
      End: () => undefined,
    };
    const move = moves[event.key];
    if (!move) return;
    event.preventDefault();
    onChange(move());
  };

  return (
    <div className={`mt-3 grid ${NAME_COLUMN} items-center gap-x-3`}>
      <span className="text-xs font-semibold">As of</span>
      <div
        role="slider"
        tabIndex={0}
        aria-label="As of"
        aria-valuemin={start}
        aria-valuemax={domain.end.getTime()}
        aria-valuenow={(shown ?? domain.end).getTime()}
        aria-valuetext={shown ? `As of ${utcText(shown)}` : "Now"}
        onKeyDown={onKeyDown}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={() => setDragging(undefined)}
        className="relative h-6 cursor-pointer touch-none rounded focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
      >
        <span aria-hidden="true" className="absolute top-1/2 right-0 left-0 h-1 -translate-y-1/2 rounded-full bg-sunken" />
        {stops.map((stop) => (
          <span
            key={stop.getTime()}
            aria-hidden="true"
            className="absolute top-1/2 h-2 w-px -translate-y-1/2 bg-line-strong"
            style={{ left: `${((stop.getTime() - start) / span) * 100}%` }}
          />
        ))}
        <span
          data-handle=""
          aria-hidden="true"
          className="absolute top-1/2 h-5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-sm border border-ink bg-surface"
          style={{ left: `${left}%` }}
        />
      </div>
    </div>
  );
}
