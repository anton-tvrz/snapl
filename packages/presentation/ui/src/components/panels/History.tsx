import { useId } from "react";
import { Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import type { HistoryResponse } from "../../api/types";
import { TONES } from "../../lib/encoding";
import { type Counts, historyRows, markerText } from "../../lib/history";
import { type HistoryBar, historyBars } from "../../lib/historyBars";
import { ageText, utcText } from "../../lib/time";
import { PanelFrame } from "../ViewFrame";

interface Props {
  history: HistoryResponse | undefined;
  /** For links from the number row. */
  id?: string;
  now: Date;
  error?: Error | null;
}

const BUCKET_WORDS: Record<string, string> = { "1h": "hour", "1d": "day", "1w": "week", "5m": "5 minutes" };

const pair = (c: Counts) => (c.succeeded === null ? "" : `${c.succeeded} / ${c.failed ?? 0}`);

// Each mark's name, with how to tell it from the others without colour: a
// failure is hatched, a scheduled repair is outlined, and each outcome has its glyph.
const LEGEND: readonly { key: string; glyph?: string; text: string; swatch: "scans" | "line" | "ok" | "fail" | "ok-outline" | "fail-outline" }[] = [
  { key: "scans", text: "Scans", swatch: "scans" },
  { key: "mostDrifted", text: "Most drifted devices (line)", swatch: "line" },
  { key: "deploysOk", glyph: "✓", text: "Deploys that succeeded", swatch: "ok" },
  { key: "deploysFailed", glyph: "✕", text: "Deploys that failed (hatched)", swatch: "fail" },
  { key: "repairsOk", glyph: "✓", text: "Scheduled repairs that succeeded (outlined)", swatch: "ok-outline" },
  { key: "repairsFailed", glyph: "✕", text: "Scheduled repairs that failed (outlined, hatched)", swatch: "fail-outline" },
];

const SWATCH: Record<(typeof LEGEND)[number]["swatch"], React.CSSProperties> = {
  scans: { backgroundColor: "var(--series-1)" },
  line: { backgroundColor: "var(--series-2)", height: 2 },
  ok: { backgroundColor: TONES.ok.fill, border: "1px solid var(--ink)" },
  fail: {
    background: `repeating-linear-gradient(135deg, ${TONES.fail.fill} 0 2px, transparent 2px 4px)`,
    border: "1px solid var(--ink)",
  },
  "ok-outline": { border: `2px solid ${TONES.ok.fill}` },
  "fail-outline": {
    background: `repeating-linear-gradient(135deg, ${TONES.fail.fill} 0 1px, transparent 1px 4px)`,
    border: `2px solid ${TONES.fail.fill}`,
  },
};

const NAMES: Record<string, string> = Object.fromEntries(LEGEND.map((entry) => [entry.key, entry.text]));

/** The buckets as a chart. A quiet bucket keeps its place on the axis and draws nothing. */
function Chart({ title, bars, long }: { title: string; bars: HistoryBar[]; long: boolean }) {
  const hatch = `hatch-${useId().replace(/[^a-zA-Z0-9]/g, "")}`;
  const tick = (t: number) => (long ? utcText(new Date(t)).slice(5, 10) : utcText(new Date(t)).slice(11, 16));
  return (
    <div
      role="img"
      aria-label={`${title}, as a chart: scans, the most drifted devices, deploys and scheduled repairs in each bucket. The same numbers are in the table below.`}
      className="h-60"
    >
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={bars} margin={{ top: 8, right: 8, bottom: 0, left: 0 }} barCategoryGap="20%" barGap={1}>
          <defs>
            <pattern id={hatch} width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <rect width="2" height="4" fill={TONES.fail.fill} />
            </pattern>
          </defs>
          <CartesianGrid stroke="var(--line)" strokeOpacity={0.6} vertical={false} />
          <XAxis dataKey="t" tickFormatter={tick} axisLine={false} tickLine={false} stroke="var(--muted)" fontSize={12} minTickGap={24} />
          <YAxis allowDecimals={false} axisLine={false} tickLine={false} stroke="var(--muted)" fontSize={12} width={32} />
          <Tooltip
            cursor={{ fill: "var(--sunken)" }}
            labelFormatter={(t) => utcText(new Date(Number(t)))}
            formatter={(value, name) => [value as number, NAMES[String(name)] ?? String(name)]}
            contentStyle={{ background: "var(--surface)", border: "1px solid var(--line)", color: "var(--ink)" }}
          />
          <Bar dataKey="scans" fill="var(--series-1)" isAnimationActive={false} />
          <Bar dataKey="deploysOk" stackId="deploys" fill={TONES.ok.fill} stroke="var(--ink)" strokeWidth={1} isAnimationActive={false} />
          <Bar dataKey="deploysFailed" stackId="deploys" fill={`url(#${hatch})`} stroke="var(--ink)" strokeWidth={1} isAnimationActive={false} />
          <Bar dataKey="repairsOk" stackId="repairs" fill="var(--surface)" stroke={TONES.ok.fill} strokeWidth={2} isAnimationActive={false} />
          <Bar dataKey="repairsFailed" stackId="repairs" fill={`url(#${hatch})`} fillOpacity={0.5} stroke={TONES.fail.fill} strokeWidth={2} isAnimationActive={false} />
          <Line dataKey="mostDrifted" type="stepAfter" stroke="var(--series-2)" strokeWidth={2} dot={{ r: 3 }} connectNulls={false} isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

function Legend() {
  return (
    <ul aria-label="What the chart shows" className="mt-2 flex flex-wrap gap-x-4 gap-y-2 text-meta text-muted">
      {LEGEND.map((entry) => (
        <li key={entry.key} className="flex items-center gap-2">
          <span aria-hidden="true" className="inline-block h-3 w-3 shrink-0" style={SWATCH[entry.swatch]} />
          <span>{entry.glyph ? `${entry.glyph} ${entry.text}` : entry.text}</span>
        </li>
      ))}
    </ul>
  );
}

/**
 * 010's history (FR-022 there), as a chart with its table one click away
 * (spec 014 FR-020, FR-021): per bucket, the scans, the most drifted
 * devices and undesired paths any scan saw, and the deploys and repairs, from
 * the audit trail. A bucket with no scan and no deploy says so.
 */
export function History({ history, now, error, id }: Props) {
  const deploys = history?.series.find((series) => series.name === "deploys");
  const word = BUCKET_WORDS[deploys?.bucket ?? "1h"] ?? "bucket";
  const title = `Drift and deploys by ${word}`;
  const rows = history ? historyRows(history) : [];
  const bars = history ? historyBars(history) : [];
  const nothing = bars.every((bar) => bar.quiet);
  const markers = deploys?.markers ?? [];

  return (
    <PanelFrame title={title} footer="Audit trail" id={id}>
      {error && (
        <p role="alert" className="mb-4 max-w-prose text-body break-words">
          History unavailable: {error.message}
        </p>
      )}
      {!history && !error && <p className="text-body text-muted">Loading…</p>}
      {history && (
        <>
          <p className="mb-2 text-meta text-muted">
            {deploys?.first_event_at
              ? `The audit trail starts ${ageText(deploys.first_event_at, now)}.`
              : "No audit events read."}
          </p>
          {nothing ? (
            <p className="text-body text-muted">No scans or deploys in this range.</p>
          ) : (
            <>
              <Chart title={title} bars={bars} long={deploys?.bucket !== "1h"} />
              <Legend />
            </>
          )}
          <details className="mt-4">
            <summary className="cursor-pointer text-meta text-muted">Show the table</summary>
          <div className="mt-2 max-h-80 overflow-auto">
            <table aria-label={title} className="w-full text-left text-body">
              <thead className="sticky top-0 bg-surface text-meta text-muted">
                <tr>
                  <th className="cell py-1 pr-4 font-medium">Start (UTC)</th>
                  <th className="cell py-1 pr-4 font-medium">Scans</th>
                  <th className="cell py-1 pr-4 font-medium">Most drifted devices</th>
                  <th className="cell py-1 pr-4 font-medium">Most undesired paths</th>
                  <th className="cell py-1 pr-4 font-medium">Deploys ok / failed</th>
                  <th className="cell py-1 font-medium">Repairs ok / failed</th>
                </tr>
              </thead>
              <tbody className="tabular-nums">
                {rows.map((row) => (
                  <tr key={row.start} className="border-t border-line">
                    <td className="py-2 pr-4 whitespace-nowrap">{utcText(new Date(row.start)).slice(0, 16)}</td>
                    {row.quiet ? (
                      <td colSpan={5} className="py-2 text-muted">
                        no scans
                      </td>
                    ) : (
                      <>
                        <td className="py-2 pr-4">{row.scans}</td>
                        <td className="py-2 pr-4">{row.mostDrifted ?? ""}</td>
                        <td className="py-2 pr-4">{row.mostUndesired ?? ""}</td>
                        <td className="py-2 pr-4">{pair(row.deploys)}</td>
                        <td className="py-2">{pair(row.repairs)}</td>
                      </>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          </details>
          {markers.length > 0 && (
            <ul aria-label="Repair runs in this range" className="mt-4 flex flex-col gap-2 text-meta text-muted">
              {markers.map((marker) => (
                <li key={`${marker.workflow_id}@${marker.t}`}>{markerText(marker, now)}</li>
              ))}
            </ul>
          )}
        </>
      )}
    </PanelFrame>
  );
}
