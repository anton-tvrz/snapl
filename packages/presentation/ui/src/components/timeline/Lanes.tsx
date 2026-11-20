import { useId, useState } from "react";

import type { DeviceLane, DriftTimeline, FabricHealth, Marker, View } from "../../api/types";
import type { RunId } from "../../data/provider";
import { driftEncoding, TONES } from "../../lib/encoding";
import { type HealthStretch, healthStretches, stretchText, stretchTitle } from "../../lib/health";
import { clock, NO_DATA, pointTime, position, type Range, type Segment, segments } from "../../lib/lanes";
import { Command } from "../Command";
import { StateMark } from "../topology/StateMark";

interface Props {
  timeline: DriftTimeline;
  /** The stretch of time drawn (lib/lanes domainOf). */
  domain: Range;
  /** The page's "now": nothing is drawn after it. */
  now: Date;
  /** The "as of" time, when one is set. */
  cursor?: Date;
  /** Why the last refresh was refused. The lanes already drawn stay. */
  error?: Error | null;
  /** The selected device's name. Its lane is shown even when it is folded away. */
  selected?: string;
  /** Shows a marker's run as the loop. Without it, markers are not buttons. */
  onSelectRun?: (run: RunId) => void;
  /** `/health` over the range (spec 016): each device's unhealthy stretches, drawn under its lane. */
  health?: View<FabricHealth>;
}

/** The width of the name column, shared with the scrubber so their times line up. */
export const NAME_COLUMN = "grid-cols-[7rem_minmax(0,1fr)]";
const LANE_GRID = `grid ${NAME_COLUMN} items-center gap-x-4`;

const MARKERS: Record<string, { tag: string; label: string }> = {
  reconcile: { tag: "R", label: "Reconcile" },
  remediation: { tag: "S", label: "Scheduled remediation" },
};

const TRIGGERS: Record<string, string> = { operator: "an operator", schedule: "the schedule" };

/** A lane in words, for anyone not reading the drawing. */
function described(lane: DeviceLane, drawn: readonly Segment[], domain: Range): string {
  const groups: { label: string; from: Date; scans: number }[] = [];
  let previous: Segment | undefined;
  for (const segment of drawn) {
    const last = groups.at(-1);
    if (segment.state === NO_DATA) {
      if (last) groups.push({ label: "no data", from: segment.start, scans: 0 });
    } else if (last && previous?.point && previous.state === segment.state) {
      last.scans += 1;
    } else {
      groups.push({ label: driftEncoding(segment.state).label, from: segment.start, scans: 1 });
    }
    previous = segment;
  }
  if (groups.length === 0) return `${lane.name}: no scan in this range`;
  const parts = groups.map((g) => {
    const count = g.scans === 0 ? "" : ` (${g.scans} ${g.scans === 1 ? "scan" : "scans"})`;
    return `${g.label} from ${clock(domain, g.from)}${count}`;
  });
  return `${lane.name}: ${parts.join(", then ")}`;
}

function markerLabel(marker: Marker, domain: Range): string {
  const kind = MARKERS[marker.kind]?.label ?? marker.kind;
  const who = TRIGGERS[marker.trigger] ?? marker.trigger;
  return `${kind}, started by ${who} at ${clock(domain, new Date(marker.t))}`;
}

function Lane({ lane, domain, now, unhealthy = [] }: { lane: DeviceLane; domain: Range; now: Date; unhealthy?: readonly HealthStretch[] }) {
  const drawn = segments(lane.points, domain, now);
  return (
    <li aria-label={lane.name} className={LANE_GRID}>
      <span className="truncate font-mono text-meta" aria-hidden="true">
        {lane.name}
        {!lane.in_intent && <span className="block font-sans text-muted">no longer in intent</span>}
      </span>
      <span className="sr-only">
        {described(lane, drawn, domain)}
        {!lane.in_intent && ", no longer in intent"}
        {unhealthy.length > 0 && `; ${unhealthy.map((stretch) => stretchText(stretch, domain)).join(", then ")}`}
      </span>
      <div className="relative h-6" aria-hidden="true">
        {drawn.map((segment) => {
          const left = position(domain, segment.start.getTime());
          const width = position(domain, segment.end.getTime()) - left;
          const encoding = driftEncoding(segment.state);
          const noData = segment.state === NO_DATA;
          return (
            <span
              key={`${segment.state}@${segment.start.getTime()}`}
              data-state={segment.state}
              title={`${noData ? "No data" : encoding.label}, ${clock(domain, segment.start)} to ${clock(domain, segment.end)}`}
              className={`absolute top-2 h-2 min-w-0.5 rounded-sm ${noData ? "lane-no-data" : ""}`}
              style={{
                left: `${left}%`,
                width: `${width}%`,
                ...(noData ? {} : { backgroundColor: TONES[encoding.tone].fill }),
              }}
            />
          );
        })}
        {lane.points.map((point) => {
          const encoding = driftEncoding(point.state);
          return (
            <svg
              key={point.scan_workflow_id}
              data-scan={point.t}
              data-shape={encoding.shape}
              viewBox="-9 -9 18 18"
              className="absolute top-1/2 h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 overflow-visible"
              style={{ left: `${position(domain, pointTime(point.t))}%` }}
            >
              <StateMark encoding={encoding} r={8} />
            </svg>
          );
        })}
        {/* Health under the drift (spec 016): a line along the lane's foot, never over the drift's colour. */}
        {unhealthy.map((stretch) => {
          const left = position(domain, stretch.start);
          return (
            <span
              key={`health@${stretch.start}`}
              data-health={stretch.open ? "open" : "closed"}
              title={stretchTitle(stretch, domain)}
              className="absolute bottom-0 h-1 min-w-0.5 rounded-sm"
              style={{ left: `${left}%`, width: `${position(domain, stretch.end) - left}%`, backgroundColor: TONES.fail.fill }}
            />
          );
        })}
      </div>
    </li>
  );
}

/**
 * One lane per device over the range (spec 011 FR-021): each scan's state as a
 * mark, held until the next scan for at most 15 minutes, stretches with no scan
 * as no data, and the runs that repair drift as markers.
 */
export function Lanes({ timeline, domain, now, cursor, error, selected, onSelectRun, health }: Props) {
  const Tag = onSelectRun ? "button" : "span";
  // Devices that left intent are folded into one row (spec 014 FR-018): their
  // lanes are mostly hatching, and they must not take the panel over.
  const current = timeline.lanes.filter((lane) => lane.in_intent);
  const gone = timeline.lanes.filter((lane) => !lane.in_intent);
  const [opened, setOpened] = useState(false);
  const open = opened || gone.some((lane) => lane.name === selected);
  const summary = useId();
  const unhealthy = new Map(
    (health?.data?.devices ?? []).map((entry) => [entry.device_id, healthStretches(entry.episodes ?? [], domain, now)]),
  );
  const anyUnhealthy = [...unhealthy.values()].some((drawn) => drawn.length > 0);
  return (
    <div>
      {error && (
        <p role="alert" className="mb-4 max-w-prose rounded border border-warn-line bg-warn-surface p-2 text-body break-words">
          {error.message}
        </p>
      )}

      {timeline.lanes.length === 0 ? (
        <div className="flex flex-col gap-2 text-body">
          <p className="text-muted">No scan recorded in this range.</p>
          <p>
            <Command text="uv run snapl scan" />
          </p>
        </div>
      ) : (
        <>
          <div className="relative pt-6">
          <ul aria-label="Drift lanes" className="flex flex-col gap-2">
            {current.map((lane) => (
              <Lane key={lane.device_id} lane={lane} domain={domain} now={now} unhealthy={unhealthy.get(lane.device_id)} />
            ))}
          </ul>

          <div className={`pointer-events-none absolute inset-0 top-6 grid ${NAME_COLUMN} gap-x-4`}>
            <div />
            <div className="relative">
              <ul aria-label="Repair runs">
                {timeline.markers.map((marker) => {
                  const kind = MARKERS[marker.kind];
                  const label = markerLabel(marker, domain);
                  return (
                    <li
                      key={`${marker.workflow_id}@${marker.t}`}
                      title={label}
                      className="absolute -top-6 bottom-0 w-0 border-l border-dashed border-line-strong"
                      style={{ left: `${position(domain, pointTime(marker.t))}%` }}
                    >
                      <Tag
                        {...(onSelectRun && {
                          type: "button" as const,
                          onClick: () => onSelectRun({ workflow_id: marker.workflow_id, started_at: marker.t }),
                        })}
                        className="chip pointer-events-auto absolute -top-0.5 -translate-x-1/2 rounded border border-line-strong bg-surface px-1 text-meta leading-4 font-semibold"
                      >
                        <span aria-hidden="true">{kind?.tag ?? "?"}</span>
                        <span className="sr-only">{label}</span>
                      </Tag>
                    </li>
                  );
                })}
              </ul>
              {cursor && (
                <span
                  data-cursor=""
                  aria-hidden="true"
                  className="absolute -top-6 bottom-0 w-0.5 -translate-x-1/2 bg-ink"
                  style={{ left: `${position(domain, cursor.getTime())}%` }}
                />
              )}
            </div>
          </div>
          </div>

          <div className={`mt-2 grid ${NAME_COLUMN} gap-x-4 text-meta text-muted`}>
            <div />
            <div className="flex justify-between">
              <span>{clock(domain, domain.start)}</span>
              <span>{clock(domain, domain.end)}</span>
            </div>
          </div>
          {gone.length > 0 && (
            <details
              aria-labelledby={summary}
              open={open}
              onToggle={(event) => setOpened(event.currentTarget.open)}
              className="mt-2 text-meta text-muted"
            >
              <summary id={summary} className="cursor-pointer">
                {gone.length} {gone.length === 1 ? "device" : "devices"} no longer in intent
              </summary>
              <ul aria-label="Drift lanes of devices no longer in intent" className="mt-2 flex flex-col gap-2 text-ink">
                {gone.map((lane) => (
                  <Lane key={lane.device_id} lane={lane} domain={domain} now={now} unhealthy={unhealthy.get(lane.device_id)} />
                ))}
              </ul>
            </details>
          )}
          <p className="mt-2 text-meta text-muted">
            <span className="font-semibold">R</span> reconcile · <span className="font-semibold">S</span> scheduled
            remediation · hatched: no scan
            {anyUnhealthy && " · underline: unhealthy"}
            {health && !health.available && ` · health: ${health.reason ?? "not read"}`}
          </p>
        </>
      )}
    </div>
  );
}
