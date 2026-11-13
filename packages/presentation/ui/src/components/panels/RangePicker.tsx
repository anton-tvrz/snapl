import { type FormEvent, useState } from "react";

import type { Range } from "../../lib/lanes";
import { PRESETS, presetRange } from "../../lib/metrics";
import { utcText } from "../../lib/time";

interface Props {
  /** The chosen range. Undefined follows now. */
  value?: Range;
  /** The server's clock: presets end here. */
  now: Date;
  onChange: (range?: Range) => void;
  /** In an archive, the one range its metrics were exported over. */
  archived?: Range;
}

const inputValue = (t: Date) => t.toISOString().slice(0, 16);

/**
 * The time range for the drift lanes and the metrics (spec 011 FR-031):
 * presets ending at the server's now, or a custom start and end in UTC. An end
 * before its start is refused here, before any request.
 */
export function RangePicker({ value, now, onChange, archived }: Props) {
  const [from, setFrom] = useState(() => inputValue(value?.start ?? new Date(now.getTime() - 3_600_000)));
  const [to, setTo] = useState(() => inputValue(value?.end ?? now));
  const [problem, setProblem] = useState<string | null>(null);

  const apply = (event: FormEvent) => {
    event.preventDefault();
    const start = new Date(`${from}:00Z`);
    const end = new Date(`${to}:00Z`);
    if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) {
      setProblem("Give both a start and an end.");
      return;
    }
    if (end <= start) {
      setProblem("The end must be after the start.");
      return;
    }
    setProblem(null);
    onChange({ start, end });
  };

  const field = "rounded border border-line bg-surface px-2 py-1 text-ink";
  return (
    <section aria-label="Time range" className="rounded-lg border border-line bg-surface p-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="mr-2 text-sm font-semibold">Time range</h2>
        {PRESETS.map((preset) => (
          <button
            key={preset.label}
            type="button"
            onClick={() => onChange(presetRange(preset, now))}
            className="rounded border border-line px-2 py-0.5"
          >
            {preset.label}
          </button>
        ))}
        {value && (
          <button type="button" onClick={() => onChange(undefined)} className="rounded border border-ink px-2 py-0.5">
            Follow now
          </button>
        )}
      </div>
      <p className="mt-2 text-xs text-muted">
        {value
          ? `${utcText(value.start)} to ${utcText(value.end)}`
          : archived
            ? "Up to the archive's time: the lanes show its last 24 h."
            : "Following now: the lanes show the last 24 h, the metrics the last hour."}
      </p>
      {archived && (
        <p className="mt-1 text-xs text-muted">
          The metrics show the exported range, {utcText(archived.start)} to {utcText(archived.end)}: an archive
          holds no other. The range here applies to the lanes and the history.
        </p>
      )}
      <form onSubmit={apply} className="mt-3 flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-xs">
          From (UTC)
          <input type="datetime-local" value={from} onChange={(e) => setFrom(e.target.value)} className={field} />
        </label>
        <label className="flex flex-col gap-1 text-xs">
          To (UTC)
          <input type="datetime-local" value={to} onChange={(e) => setTo(e.target.value)} className={field} />
        </label>
        <button type="submit" className="rounded border border-line px-3 py-1">
          Apply
        </button>
      </form>
      {problem && (
        <p role="alert" className="mt-2 text-sm">
          {problem}
        </p>
      )}
    </section>
  );
}
