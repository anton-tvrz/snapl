import { type ReactNode, useId } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { HistoryResponse, Series } from "../../api/types";
import {
  FABRIC_SERIES,
  latestRows,
  RUN_OUTCOMES,
  type RunBar,
  runBars,
  runRows,
  type RunTrigger,
  runTriggers,
  type ScanPoint,
  scanPoints,
  seriesNamed,
  timeTicks,
  TRIGGER_WORDS,
} from "../../lib/fabricHistory";
import { durationText, utcText } from "../../lib/time";
import { PanelFrame } from "../ViewFrame";
import { BUCKET_WORDS } from "./History";

interface Props {
  /** `/history` with the four series of spec 015 named. Undefined until the first answer. */
  history: HistoryResponse | undefined;
  /** The devices in intent now, drawn as a reference over the devices in sync. Undefined when not known. */
  inIntent: number | undefined;
  error?: Error | null;
  id?: string;
}

// The most rows a table lists. A month of scans every few minutes is thousands.
const TABLE_ROWS = 100;
// Above this many points a line is drawn without a dot at each one.
const DOTS_UP_TO = 60;

// One hue per trigger, in a fixed order, so a colour always follows its trigger.
const TRIGGER_COLOURS: Record<RunTrigger, string> = {
  schedule: "var(--series-1)",
  operator: "var(--series-2)",
  other: "var(--series-3)",
};

const AXIS = { axisLine: false, tickLine: false, stroke: "var(--muted)", fontSize: 12 } as const;
const TOOLTIP_STYLE = { background: "var(--surface)", border: "1px solid var(--line)", color: "var(--ink)" };

const plural = (count: number, one: string, many: string) => `${count} ${count === 1 ? one : many}`;
const scanTime = (t: number) => utcText(new Date(t)).slice(0, 19);

/** An axis tick: the time of day over a day or less, the date over longer. */
function ticker(series: Series | undefined) {
  const long = !!series && Date.parse(series.end) - Date.parse(series.start) > 36 * 3_600_000;
  return (t: number) => (long ? utcText(new Date(t)).slice(5, 10) : utcText(new Date(t)).slice(11, 16));
}

function domainOf(series: Series): [number, number] {
  return [Date.parse(series.start), Date.parse(series.end)];
}

/** One of the four charts: its name, the chart or why there is none, and its table behind a toggle. */
function Cell({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <h3 className="text-body font-medium">{title}</h3>
      {children}
    </div>
  );
}

function Toggle({ children }: { children: ReactNode }) {
  return (
    <details>
      <summary className="cursor-pointer text-meta text-muted">Show the table</summary>
      <div className="mt-2 max-h-60 overflow-auto">{children}</div>
    </details>
  );
}

function Omitted({ omitted, shown }: { omitted: number; shown: number }) {
  if (omitted === 0) return null;
  return <p className="mb-2 text-meta text-muted">{`The latest ${shown} of ${shown + omitted} scans.`}</p>;
}

function ScanTable({ label, heading, points }: { label: string; heading: string; points: ScanPoint[] }) {
  const { rows, omitted } = latestRows(points, TABLE_ROWS);
  return (
    <Toggle>
      <Omitted omitted={omitted} shown={rows.length} />
      <table aria-label={label} className="w-full text-left text-body">
        <thead className="sticky top-0 bg-surface text-meta text-muted">
          <tr>
            <th className="cell py-1 pr-4 font-medium">Scan (UTC)</th>
            <th className="cell py-1 font-medium">{heading}</th>
          </tr>
        </thead>
        <tbody className="tabular-nums">
          {rows.map((point) => (
            <tr key={point.t} className="border-t border-line">
              <td className="py-2 pr-4 whitespace-nowrap">{scanTime(point.t)}</td>
              <td className="py-2">{point.value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Toggle>
  );
}

/** A per-scan count as a stepped line: the count holds until the next scan. */
function StepChart({
  label,
  series,
  points,
  name,
  reference,
}: {
  label: string;
  series: Series;
  points: ScanPoint[];
  name: string;
  reference?: { y: number; text: string };
}) {
  const top = Math.max(reference?.y ?? 0, ...points.map((point) => point.value));
  return (
    <div role="img" aria-label={label} className="h-40">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={points} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--line)" strokeOpacity={0.6} vertical={false} />
          <XAxis dataKey="t" type="number" scale="time" domain={domainOf(series)} ticks={timeTicks(...domainOf(series))} tickFormatter={ticker(series)} minTickGap={24} {...AXIS} />
          <YAxis allowDecimals={false} domain={[0, Math.max(1, top)]} width={32} {...AXIS} />
          <Tooltip
            labelFormatter={(t) => utcText(new Date(Number(t)))}
            formatter={(value) => [value as number, name]}
            contentStyle={TOOLTIP_STYLE}
          />
          {reference && (
            <ReferenceLine
              y={reference.y}
              stroke="var(--ink)"
              strokeDasharray="4 4"
              label={{ value: reference.text, position: "insideTopRight", fill: "var(--muted)", fontSize: 12 }}
            />
          )}
          <Line
            dataKey="value"
            type="stepAfter"
            stroke="var(--series-1)"
            strokeWidth={2}
            dot={points.length <= DOTS_UP_TO ? { r: 4, fill: "var(--series-1)", stroke: "var(--surface)", strokeWidth: 2 } : false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function InSync({ series, inIntent }: { series: Series | undefined; inIntent: number | undefined }) {
  const title = "Devices in sync, by scan";
  const points = scanPoints(series);
  if (!series || points.length === 0) {
    return (
      <Cell title={title}>
        <p className="text-body text-muted">No scans in this range.</p>
      </Cell>
    );
  }
  const values = points.map((point) => point.value);
  const against = inIntent === undefined ? "" : `, against ${inIntent} in intent now`;
  const label =
    `Devices in sync at each scan, as a chart: ${plural(points.length, "scan", "scans")}, ` +
    `from ${Math.min(...values)} to ${Math.max(...values)} in sync${against}. ` +
    `The latest scan found ${values.at(-1)}. The same numbers are in the table below.`;
  return (
    <Cell title={title}>
      <StepChart
        label={label}
        series={series}
        points={points}
        name="Devices in sync"
        reference={inIntent === undefined ? undefined : { y: inIntent, text: `${inIntent} in intent now` }}
      />
      <ScanTable label="Devices in sync at each scan" heading="In sync" points={points} />
    </Cell>
  );
}

function OpenDrifts({ series }: { series: Series | undefined }) {
  const title = "Open drifts, by scan";
  const points = scanPoints(series);
  if (!series || points.length === 0) {
    return (
      <Cell title={title}>
        <p className="text-body text-muted">No scans in this range.</p>
      </Cell>
    );
  }
  const values = points.map((point) => point.value);
  const label =
    `Open drifts after each scan, as a chart: ${plural(points.length, "scan", "scans")}, at most ${Math.max(...values)} open. ` +
    `The latest scan left ${values.at(-1)} open. The same numbers are in the table below.`;
  return (
    <Cell title={title}>
      <StepChart label={label} series={series} points={points} name="Open drifts" />
      <ScanTable label="Open drifts after each scan" heading="Open" points={points} />
    </Cell>
  );
}

function runTotals(bars: RunBar[]) {
  let runs = 0;
  let failed = 0;
  let running = 0;
  for (const bar of bars) {
    runs += bar.total ?? 0;
    for (const trigger of ["schedule", "operator", "other"] as const) {
      failed += bar[`${trigger}.failed`] ?? 0;
      running += bar[`${trigger}.running`] ?? 0;
    }
  }
  return { runs, failed, running };
}

function RunsLegend({ triggers, failed, running }: { triggers: RunTrigger[]; failed: boolean; running: boolean }) {
  return (
    <ul aria-label="What the runs chart shows" className="flex flex-wrap gap-x-4 gap-y-2 text-meta text-muted">
      {triggers.map((trigger) => (
        <li key={trigger} className="flex items-center gap-2">
          <span aria-hidden="true" className="inline-block h-3 w-3 shrink-0" style={{ backgroundColor: TRIGGER_COLOURS[trigger] }} />
          <span>{TRIGGER_WORDS[trigger]}</span>
        </li>
      ))}
      {failed && (
        <li className="flex items-center gap-2">
          <span
            aria-hidden="true"
            className="inline-block h-3 w-3 shrink-0"
            style={{ background: "repeating-linear-gradient(135deg, var(--muted) 0 2px, transparent 2px 4px)", border: "1px solid var(--ink)" }}
          />
          <span>✕ Failed (hatched)</span>
        </li>
      )}
      {running && (
        <li className="flex items-center gap-2">
          <span aria-hidden="true" className="inline-block h-3 w-3 shrink-0" style={{ border: "2px solid var(--muted)" }} />
          <span>… Running (outlined)</span>
        </li>
      )}
    </ul>
  );
}

function Runs({ series }: { series: Series | undefined }) {
  const hatch = `runs-${useId().replace(/[^a-zA-Z0-9]/g, "")}`;
  const word = BUCKET_WORDS[series?.bucket ?? "1h"] ?? "bucket";
  const title = `Runs by ${word}`;
  const bars = runBars(series);
  if (!series || bars.every((bar) => bar.quiet)) {
    return (
      <Cell title={title}>
        <p className="text-body text-muted">No runs in this range.</p>
      </Cell>
    );
  }
  const triggers = runTriggers(bars);
  const totals = runTotals(bars);
  const failedText = totals.failed === 0 ? "none failed" : `${totals.failed} failed`;
  const runningText = totals.running === 0 ? "" : `, ${totals.running} running`;
  const label =
    `Runs started in each ${word}, by trigger, as a chart: ${plural(totals.runs, "run", "runs")}, ${failedText}${runningText}. ` +
    "The same numbers are in the table below.";
  const tick = ticker(series);
  return (
    <Cell title={title}>
      <div role="img" aria-label={label} className="h-40">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={bars} margin={{ top: 8, right: 8, bottom: 0, left: 0 }} barCategoryGap="20%">
            <defs>
              {triggers.map((trigger) => (
                <pattern key={trigger} id={`${hatch}-${trigger}`} width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                  <rect width="2" height="4" fill={TRIGGER_COLOURS[trigger]} />
                </pattern>
              ))}
            </defs>
            <CartesianGrid stroke="var(--line)" strokeOpacity={0.6} vertical={false} />
            <XAxis dataKey="t" tickFormatter={tick} minTickGap={24} {...AXIS} />
            <YAxis allowDecimals={false} width={32} {...AXIS} />
            <Tooltip
              cursor={{ fill: "var(--sunken)" }}
              labelFormatter={(t) => utcText(new Date(Number(t)))}
              formatter={(value, key) => {
                const [trigger, outcome] = String(key).split(".") as [RunTrigger, string];
                return [value as number, `${TRIGGER_WORDS[trigger]}, ${outcome}`];
              }}
              contentStyle={TOOLTIP_STYLE}
            />
            {triggers.flatMap((trigger) =>
              RUN_OUTCOMES.map((outcome) => {
                const colour = TRIGGER_COLOURS[trigger];
                const look =
                  outcome === "succeeded"
                    ? { fill: colour, stroke: "var(--surface)", strokeWidth: 1 }
                    : outcome === "failed"
                      ? { fill: `url(#${hatch}-${trigger})`, stroke: "var(--ink)", strokeWidth: 1 }
                      : { fill: "var(--surface)", stroke: colour, strokeWidth: 2 };
                return <Bar key={`${trigger}.${outcome}`} dataKey={`${trigger}.${outcome}`} stackId="runs" isAnimationActive={false} {...look} />;
              }),
            )}
          </BarChart>
        </ResponsiveContainer>
      </div>
      <RunsLegend triggers={triggers} failed={totals.failed > 0} running={totals.running > 0} />
      <Toggle>
        <table aria-label={title} className="w-full text-left text-body">
          <thead className="sticky top-0 bg-surface text-meta text-muted">
            <tr>
              <th className="cell py-1 pr-4 font-medium">Start (UTC)</th>
              <th className="cell py-1 pr-4 font-medium">Runs</th>
              <th className="cell py-1 font-medium">By trigger</th>
            </tr>
          </thead>
          <tbody className="tabular-nums">
            {runRows(series).map((row) => (
              <tr key={row.t} className="border-t border-line">
                <td className="py-2 pr-4 whitespace-nowrap">{utcText(new Date(row.t)).slice(0, 16)}</td>
                <td className="py-2 pr-4">{row.total ?? ""}</td>
                <td className={row.total === null ? "py-2 text-muted" : "py-2"}>{row.text}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Toggle>
    </Cell>
  );
}

function Repairs({ series }: { series: Series | undefined }) {
  const title = "Repair durations";
  const points = scanPoints(series);
  if (!series || points.length === 0) {
    return (
      <Cell title={title}>
        <p className="text-body text-muted">No drift repaired in this range.</p>
      </Cell>
    );
  }
  const longest = Math.max(...points.map((point) => point.value));
  const label =
    `Each repaired drift's duration, scan to scan, as a chart: ${plural(points.length, "repair", "repairs")}, ` +
    `the longest ${durationText(longest)}. The same numbers are in the table below.`;
  const { rows, omitted } = latestRows(points, TABLE_ROWS);
  return (
    <Cell title={title}>
      <div role="img" aria-label={label} className="h-40">
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="var(--line)" strokeOpacity={0.6} vertical={false} />
            <XAxis dataKey="t" type="number" scale="time" domain={domainOf(series)} ticks={timeTicks(...domainOf(series))} tickFormatter={ticker(series)} minTickGap={24} {...AXIS} />
            <YAxis dataKey="value" type="number" domain={[0, "auto"]} tickFormatter={(s: number) => durationText(s)} width={56} {...AXIS} />
            <Tooltip
              labelFormatter={() => ""}
              formatter={(value, key) => (key === "t" ? [scanTime(Number(value)), "Repaired at"] : [durationText(Number(value)), "Scan to scan"])}
              contentStyle={TOOLTIP_STYLE}
            />
            <Scatter data={points} fill="var(--series-1)" stroke="var(--surface)" strokeWidth={2} isAnimationActive={false} />
          </ScatterChart>
        </ResponsiveContainer>
      </div>
      <p className="text-meta text-muted">Each point is one repaired drift, from the scan that saw it to the scan that saw it gone.</p>
      <Toggle>
        {omitted > 0 && <p className="mb-2 text-meta text-muted">{`The latest ${rows.length} of ${points.length} repairs.`}</p>}
        <table aria-label="Repaired drifts, scan to scan" className="w-full text-left text-body">
          <thead className="sticky top-0 bg-surface text-meta text-muted">
            <tr>
              <th className="cell py-1 pr-4 font-medium">Repaired (UTC)</th>
              <th className="cell py-1 font-medium">Scan to scan</th>
            </tr>
          </thead>
          <tbody className="tabular-nums">
            {rows.map((point) => (
              <tr key={point.t} className="border-t border-line">
                <td className="py-2 pr-4 whitespace-nowrap">{scanTime(point.t)}</td>
                <td className="py-2">{durationText(point.value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Toggle>
    </Cell>
  );
}

/**
 * The fabric over the page's range (spec 015 FR-005, US2), from the audit
 * trail: the devices in sync and the open drifts at each scan, the runs per
 * bucket by trigger, and each repaired drift's duration. Each chart has a
 * text alternative and its numbers in a table; an empty range says so.
 */
export function FabricHistory({ history, inIntent, error, id }: Props) {
  const held = FABRIC_SERIES.some((name) => seriesNamed(history, name));
  return (
    <PanelFrame title="Fabric history" footer="Audit trail" id={id}>
      {error && (
        <p role="alert" className="mb-4 max-w-prose text-body break-words">
          Fabric history unavailable: {error.message}
        </p>
      )}
      {!history && !error && <p className="text-body text-muted">Loading…</p>}
      {history && !held && (
        <p className="text-body text-muted">Not in this recording: it was made before the fabric history existed.</p>
      )}
      {history && held && (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <InSync series={seriesNamed(history, "in_sync_devices")} inIntent={inIntent} />
          <OpenDrifts series={seriesNamed(history, "open_drifts")} />
          <Runs series={seriesNamed(history, "runs")} />
          <Repairs series={seriesNamed(history, "repair_duration")} />
        </div>
      )}
    </PanelFrame>
  );
}
