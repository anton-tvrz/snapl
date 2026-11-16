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
  // The custom fields are one click away, and open while they hold a problem to read.
  const [custom, setCustom] = useState(false);

  const apply = (event: FormEvent) => {
    event.preventDefault();
    const start = new Date(`${from}:00Z`);
    const end = new Date(`${to}:00Z`);
    if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) {
      setProblem("Give both a start and an end.");
      setCustom(true);
      return;
    }
    if (end <= start) {
      setProblem("The end must be after the start.");
      setCustom(true);
      return;
    }
    setProblem(null);
    onChange({ start, end });
  };

  const chip = "chip rounded border px-2 py-0.5";
  const field = "chip rounded border border-line bg-surface px-2 py-1 text-ink";
  return (
    <section
      aria-label="Time range"
      data-toolbar=""
      className="flex flex-col gap-2 rounded-lg border border-panel-line bg-surface px-6 py-4 text-body shadow-panel"
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <h2 className="text-meta font-semibold tracking-widest text-muted uppercase">Time range</h2>
        <div className="flex flex-wrap items-center gap-2">
          {PRESETS.map((preset) => (
            <button
              key={preset.label}
              type="button"
              onClick={() => onChange(presetRange(preset, now))}
              className={`${chip} border-line`}
            >
              {preset.label}
            </button>
          ))}
          {value && (
            <button type="button" onClick={() => onChange(undefined)} className={`${chip} border-ink`}>
              Follow now
            </button>
          )}
        </div>
        <p className="min-w-0 flex-1 text-meta text-muted">
          {value
            ? `${utcText(value.start)} to ${utcText(value.end)}`
            : archived
              ? "Up to the archive's time: the lanes show its last 24 h."
              : "Following now: the lanes show the last 24 h, the metrics the last hour."}
        </p>
        <details open={custom} onToggle={(event) => setCustom(event.currentTarget.open)} className="text-meta">
          <summary className="cursor-pointer text-muted">Custom range</summary>
          <form onSubmit={apply} className="mt-2 flex flex-wrap items-end gap-4">
            <label className="flex flex-col gap-2">
              From (UTC)
              <input type="datetime-local" value={from} onChange={(e) => setFrom(e.target.value)} className={field} />
            </label>
            <label className="flex flex-col gap-2">
              To (UTC)
              <input type="datetime-local" value={to} onChange={(e) => setTo(e.target.value)} className={field} />
            </label>
            <button type="submit" className={`${chip} border-line text-body`}>
              Apply
            </button>
          </form>
          {problem && (
            <p role="alert" className="mt-2 text-body">
              {problem}
            </p>
          )}
        </details>
      </div>
      {archived && (
        <p className="max-w-prose text-meta text-muted">
          The metrics show the exported range, {utcText(archived.start)} to {utcText(archived.end)}: an archive
          holds no other. The range here applies to the lanes and the history.
        </p>
      )}
    </section>
  );
}
