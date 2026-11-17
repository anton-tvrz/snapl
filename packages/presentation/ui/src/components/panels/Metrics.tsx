import { Area, CartesianGrid, ComposedChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import type { MetricsData, MetricsViewName, View } from "../../api/types";
import type { DataProvider } from "../../data/provider";
import { usePolling } from "../../data/usePolling";
import { TONES } from "../../lib/encoding";
import type { Range } from "../../lib/lanes";
import { formatValue, latest, leadValue, type MetricKind, seriesLabel, UNIT_NAMES, upState } from "../../lib/metrics";
import { durationText, utcText } from "../../lib/time";
import { ViewFrame } from "../ViewFrame";

// The series palette has six validated slots, used in order and never cycled.
const SLOTS = 6;
const METRICS_MS = 30_000;

const clock = (t: number) => utcText(new Date(t)).slice(11, 16);

function Lines({ title, data }: { title: string; data: MetricsData }) {
  const drawn = (data.series ?? []).slice(0, SLOTS);
  const rows = new Map<number, Record<string, number | null>>();
  drawn.forEach((series, index) => {
    for (const point of series.points) {
      const t = Date.parse(point.t);
      const row = rows.get(t) ?? { t };
      row[`s${index}`] = point.value ?? null;
      rows.set(t, row);
    }
  });
  const points = [...rows.values()].sort((a, b) => (a.t ?? 0) - (b.t ?? 0));
  const format = (value: unknown) => formatValue(typeof value === "number" ? value : null, data.unit);

  return (
    <div role="img" aria-label={`${title} over time, as lines. The latest values are in the table.`} className="h-48">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={points} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--line)" strokeOpacity={0.6} vertical={false} />
          <XAxis
            dataKey="t"
            type="number"
            scale="time"
            domain={[Date.parse(data.start), Date.parse(data.end)]}
            tickFormatter={clock}
            axisLine={false}
            tickLine={false}
            stroke="var(--muted)"
            fontSize={12}
          />
          {/* A count is shown whole, so its ticks are whole too: 0.25 would read as 0. */}
          <YAxis allowDecimals={data.unit !== "count"} tickFormatter={format} axisLine={false} tickLine={false} stroke="var(--muted)" fontSize={12} width={64} />
          <Tooltip
            labelFormatter={(t) => utcText(new Date(Number(t)))}
            formatter={(value) => format(value)}
            contentStyle={{ background: "var(--surface)", border: "1px solid var(--line)", color: "var(--ink)" }}
          />
          {drawn.map((series, index) => (
            <Area
              key={seriesLabel(series.labels)}
              dataKey={`s${index}`}
              name={seriesLabel(series.labels)}
              stroke={`var(--series-${index + 1})`}
              strokeWidth={2}
              // A light fill under each line. A gap in the data stays a gap.
              fill={`var(--series-${index + 1})`}
              fillOpacity={0.08}
              dot={false}
              connectNulls={false}
              isAnimationActive={false}
              type="linear"
            />
          ))}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

function Strips({ title, data }: { title: string; data: MetricsData }) {
  const start = Date.parse(data.start);
  const span = Math.max(1, Date.parse(data.end) - start);
  const at = (t: number) => Math.min(100, Math.max(0, ((t - start) / span) * 100));
  return (
    <ul aria-label={`${title} over time`} className="flex flex-col gap-2">
      {(data.series ?? []).map((series) => {
        const label = seriesLabel(series.labels);
        const runs: { state: string; from: number; to: number }[] = [];
        let down = 0;
        let gap = 0;
        for (const point of series.points) {
          const state = upState(point.value);
          const t = Date.parse(point.t);
          if (state === "down") down += data.step;
          if (state === "gap") gap += data.step;
          const last = runs.at(-1);
          if (last && last.state === state) last.to = t + data.step * 1000;
          else runs.push({ state, from: t, to: t + data.step * 1000 });
        }
        const summary = [
          down > 0 ? `down ${durationText(down)} of ${durationText(span / 1000)}` : "up throughout",
          gap > 0 ? `no data ${durationText(gap)}` : "",
        ]
          .filter(Boolean)
          .join(", ");
        return (
          <li key={label} aria-label={`${label}: ${summary}`} className="grid grid-cols-[7rem_minmax(0,1fr)] items-center gap-x-4">
            <span aria-hidden="true" className="truncate font-mono text-meta">
              {label}
            </span>
            <span aria-hidden="true" className="relative h-3">
              {runs.map((run) => (
                <span
                  key={run.from}
                  data-state={run.state}
                  title={`${run.state === "gap" ? "no data" : run.state}, ${clock(run.from)} to ${clock(run.to)}`}
                  className={`absolute top-0 h-3 min-w-px ${run.state === "gap" ? "lane-no-data" : ""}`}
                  style={{
                    left: `${at(run.from)}%`,
                    width: `${at(run.to) - at(run.from)}%`,
                    ...(run.state === "gap" ? {} : { backgroundColor: TONES[run.state === "up" ? "ok" : "fail"].fill }),
                  }}
                />
              ))}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

interface PanelProps {
  title: string;
  /** What the value is; a count of events leads with the window it covers (spec 015). */
  kind?: MetricKind | null;
  view: View<MetricsData> | undefined;
  now: Date;
  /** Why the last request failed, such as a range the server refused. */
  error?: Error | null;
}

/** One metrics view (spec 012): its chart, and the latest value of each series as a table. */
export function MetricPanel({ title, kind, view, now, error }: PanelProps) {
  const data = view?.data;
  const all = data?.series ?? [];
  const lead = data ? leadValue(data, kind) : null;
  // The legend names what the chart draws: up/down strips name themselves.
  const legend = data && data.unit !== "up" ? all.slice(0, SLOTS) : [];
  return (
    <ViewFrame title={title} view={view} now={now} error={error}>
      {view && error && (
        <p role="alert" className="mb-2 max-w-prose text-body break-words">
          {error.message}
        </p>
      )}
      {data && (
        <div className="flex flex-col gap-2 text-body">
          {lead && (
            <p className="flex flex-wrap items-baseline gap-x-2">
              <span data-lead="" className="text-number font-semibold tabular-nums">
                {lead.text}
              </span>
              {lead.window && <span className="text-meta">{lead.window}</span>}
              {lead.series && <span className="text-meta text-muted">{lead.series}</span>}
            </p>
          )}
          {legend.length > 1 && (
            <ul aria-label={`${title}: series`} className="flex flex-wrap gap-x-4 gap-y-2 text-meta text-muted">
              {legend.map((series, index) => (
                <li key={seriesLabel(series.labels)} className="flex items-center gap-2">
                  <span
                    aria-hidden="true"
                    className="inline-block h-2 w-2 shrink-0 rounded-full"
                    style={{ backgroundColor: `var(--series-${index + 1})` }}
                  />
                  <span>
                    {seriesLabel(series.labels)} <span className="text-ink tabular-nums">{formatValue(latest(series), data.unit)}</span>
                  </span>
                </li>
              ))}
            </ul>
          )}
          {all.length === 0 ? (
            <p className="text-muted">No data in this range.</p>
          ) : data.unit === "up" ? (
            <Strips title={title} data={data} />
          ) : (
            <Lines title={title} data={data} />
          )}
          {all.length > SLOTS && data.unit !== "up" && (
            <p className="text-meta text-muted">
              The chart draws the first {SLOTS} series; all {all.length} are in the table.
            </p>
          )}
          {data.note && <p className="text-meta text-muted">{data.note}</p>}
          <p className="text-meta text-muted">
            {UNIT_NAMES[data.unit] ?? data.unit}
            {/* A rare rate is reworded, and the panel says so (spec 014 FR-026). */}
            {data.unit === "per_second" && ", shown per minute below one a second"} · step {durationText(data.step)}
          </p>
          {all.length > 0 && (
            <details>
              <summary className="cursor-pointer text-meta text-muted">Latest values</summary>
              <table aria-label={`${title}: latest values`} className="mt-2 w-full text-left text-body">
                <thead className="text-meta text-muted">
                  <tr>
                    <th className="cell py-1 pr-4 font-medium">Series</th>
                    <th className="cell py-1 font-medium">Latest</th>
                  </tr>
                </thead>
                <tbody>
                  {all.map((series, index) => (
                    <tr key={seriesLabel(series.labels)} className="border-t border-line">
                      <td className="py-2 pr-4">
                        {data.unit !== "up" && index < SLOTS && (
                          <span
                            aria-hidden="true"
                            className="mr-2 inline-block h-2 w-2 rounded-full"
                            style={{ backgroundColor: `var(--series-${index + 1})` }}
                          />
                        )}
                        {seriesLabel(series.labels)}
                      </td>
                      <td className="py-2 tabular-nums">{formatValue(latest(series), data.unit)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}
        </div>
      )}
    </ViewFrame>
  );
}

interface ViewProps {
  provider: DataProvider;
  name: MetricsViewName;
  title: string;
  kind?: MetricKind | null;
  /** The chosen range, or undefined for the server's default (the last hour). */
  range?: Range;
  now: Date;
  /** False in an archive: the answer never changes, so it is asked once. */
  live?: boolean;
}

/** One metrics view, asked for every 30 s and again when the range changes. */
export function MetricView({ provider, name, title, kind, range, now, live = true }: ViewProps) {
  const key = range ? `${range.start.toISOString()}/${range.end.toISOString()}` : "default";
  const answer = usePolling(() => provider.metrics(name, range), live ? METRICS_MS : null, key);
  return <MetricPanel title={title} kind={kind} view={answer.data?.metrics} now={now} error={answer.error} />;
}
