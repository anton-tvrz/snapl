import type { KeyboardEvent } from "react";

import type { DeviceView, Topology as TopologyData } from "../../api/types";
import type { Activity, FabricActivity } from "../../lib/activity";
import { driftEncoding, runEncoding, TONES } from "../../lib/encoding";
import { healthMark, healthTitle } from "../../lib/health";
import { layoutTiers } from "../../lib/layout";
import { useReducedMotion } from "../../lib/useReducedMotion";
import { DeviceList } from "./DeviceList";
import { Legend } from "./Legend";
import { StateMark } from "./StateMark";

interface Props {
  topology: TopologyData;
  /** Each device's state. A device with no entry is drawn as never scanned. */
  devices: readonly DeviceView[];
  activity: FabricActivity;
  /** Keys of the links that carry drift (`markedLinks`). */
  marked: ReadonlySet<string>;
  /** Device name -> consecutive failed scheduled repairs (010 FR-020). */
  flagged: ReadonlyMap<string, number>;
  selected: string | undefined;
  onSelect: (name: string) => void;
}

const SIZE = { width: 960, height: 300 };
const R = 20;

function activityText(activity: Activity | undefined): string | null {
  if (!activity) return null;
  if (activity.kind === "active") return `${activity.workflowType} running`;
  return `${activity.workflowType} just ended: ${runEncoding(activity.outcome ?? "").label}`;
}

/** The health marks on screen, in the legend's row (spec 016). A healthy device has no mark. */
function HealthKey({ states }: { states: readonly (string | undefined)[] }) {
  const marks = [...new Set(states.filter((state): state is string => state !== undefined))]
    .map(healthMark)
    .filter((mark) => mark.glyph !== null)
    .sort((a, b) => Number(a.tone !== "fail") - Number(b.tone !== "fail") || a.label.localeCompare(b.label));
  if (marks.length === 0) return null;
  return (
    <ul aria-label="Health marks shown" className="flex flex-wrap gap-x-6 gap-y-2 text-meta text-muted">
      {marks.map((mark) => (
        <li key={mark.label} className="flex items-center gap-2">
          <span
            aria-hidden="true"
            className="flex h-4 w-4 items-center justify-center rounded-full font-semibold"
            style={{ backgroundColor: TONES[mark.tone].fill, color: TONES[mark.tone].onFill }}
          >
            {mark.glyph}
          </span>
          <span>{mark.label}</span>
        </li>
      ))}
    </ul>
  );
}

/**
 * The fabric as it is built: devices in tiers by role, the links intent
 * declares between them, each device's drift state, and where work is
 * happening (spec 011 US1). Every device is a button, in tier order.
 */
export function Topology({ topology, devices, activity, marked, flagged, selected, onSelect }: Props) {
  const reducedMotion = useReducedMotion();
  const layout = layoutTiers(topology.nodes, topology.links, SIZE);
  const byName = new Map(devices.map((device) => [device.name, device]));
  const fabricText = activityText(activity.fabric ?? undefined);

  if (layout.kind === "too-many") {
    // The list needs a row per device in intent, scanned or not.
    const rows = topology.nodes.map(
      (node): DeviceView =>
        byName.get(node.name) ?? {
          device_id: node.device_id,
          name: node.name,
          role: node.role,
          in_intent: true,
          drift_state: "never_scanned",
        },
    );
    return (
      <div>
        <p className="mb-4 text-body text-muted">
          {layout.count} devices is more than the {layout.limit} this drawing fits on one screen. Showing the list.
        </p>
        <DeviceList devices={rows} selected={selected} onSelect={onSelect} />
      </div>
    );
  }

  const onKey = (event: KeyboardEvent, name: string) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect(name);
    }
  };

  return (
    <div>
      {fabricText && (
        <p role="status" className="mb-2 text-meta text-muted">
          {fabricText} on the fabric
        </p>
      )}
      <svg viewBox={`0 0 ${SIZE.width} ${SIZE.height}`} className="h-auto w-full" role="group" aria-label="Fabric topology">
        <g>
          {layout.links.map((drawn) => {
            const carriesDrift = marked.has(drawn.key);
            const ends = `${drawn.link.a.device} ${drawn.link.a.interface} to ${drawn.link.b.device} ${drawn.link.b.interface}`;
            return (
              <g key={drawn.key} data-link={drawn.key} data-marked={carriesDrift}>
                <title>{carriesDrift ? `${ends}: carries drift` : ends}</title>
                <line
                  x1={drawn.x1}
                  y1={drawn.y1}
                  x2={drawn.x2}
                  y2={drawn.y2}
                  stroke={carriesDrift ? "var(--ink)" : "var(--line-strong)"}
                  strokeWidth={carriesDrift ? 3 : 1.5}
                  // A drifted link is thicker and dashed: told by more than its colour.
                  strokeDasharray={carriesDrift ? "8 5" : undefined}
                />
                {!drawn.link.agreed && <line x1={drawn.x1} y1={drawn.y1} x2={drawn.x2} y2={drawn.y2} stroke="var(--surface)" strokeWidth={1} strokeDasharray="2 6" />}
              </g>
            );
          })}
        </g>
        <g>
          {layout.tiers.map((tier) => (
            <text key={tier.role} x={8} y={tier.y} dominantBaseline="central" fontSize={11} fill="var(--muted)">
              {tier.role}
            </text>
          ))}
        </g>
        {layout.nodes.map((placed) => {
          // The top tier's links leave downwards, so its names go above the mark.
          const above = placed.y === layout.tiers[0]?.y && layout.tiers.length > 1;
          const device = byName.get(placed.name);
          const encoding = driftEncoding(device?.drift_state ?? "never_scanned");
          const here = activity.devices.get(placed.name);
          const failures = flagged.get(placed.name);
          // Health sits beside the drift state and never changes it (spec 016 FR-010).
          const health = device?.health ?? null;
          const mark = health ? healthMark(health.state) : null;
          const label = [
            placed.name,
            placed.role,
            encoding.label,
            mark?.label,
            activityText(here),
            failures ? `repair failed ${failures} times in a row` : null,
          ]
            .filter(Boolean)
            .join(", ");
          return (
            <g
              key={placed.name}
              role="button"
              tabIndex={0}
              aria-label={label}
              aria-pressed={selected === placed.name}
              data-y={placed.y}
              data-shape={encoding.shape}
              transform={`translate(${placed.x} ${placed.y})`}
              onClick={() => onSelect(placed.name)}
              onKeyDown={(event) => onKey(event, placed.name)}
              className="device cursor-pointer"
            >
              {mark?.glyph && health && <title>{healthTitle(health)}</title>}
              {/* A surface disc under the mark, so links do not show through it. */}
              <circle r={R + 6} fill="var(--surface)" />
              {selected === placed.name && <circle r={R + 6} fill="none" stroke="var(--ink)" strokeWidth={2} />}
              {here && (
                <circle
                  r={R + 6}
                  fill="none"
                  stroke="var(--ink)"
                  strokeWidth={2}
                  strokeDasharray={here.kind === "recent" ? "2 4" : undefined}
                  data-activity={here.kind}
                  className={reducedMotion ? "activity-still" : "activity-pulse"}
                />
              )}
              <StateMark encoding={encoding} r={R} />
              {mark?.glyph && (
                // Top left, opposite the repair failures' count.
                <g transform={`translate(${-R} ${-R})`} aria-hidden="true" data-health={health?.state}>
                  <circle r={8} fill={TONES[mark.tone].fill} stroke="var(--surface)" strokeWidth={1.5} />
                  <text textAnchor="middle" dominantBaseline="central" fontSize={11} fontWeight={700} fill={TONES[mark.tone].onFill}>
                    {mark.glyph}
                  </text>
                </g>
              )}
              {failures && (
                <g transform={`translate(${R} ${-R})`} aria-hidden="true">
                  <circle r={8} fill="var(--surface)" stroke="var(--ink)" strokeWidth={1.5} />
                  <text textAnchor="middle" dominantBaseline="central" fontSize={10} fontWeight={700} fill="var(--ink)">
                    {failures}
                  </text>
                </g>
              )}
              <text y={above ? -(R + 14) : R + 22} textAnchor="middle" fontSize={12} fill="var(--ink)" className="font-mono">
                {placed.name}
              </text>
            </g>
          );
        })}
      </svg>
      <div className="mt-2 flex flex-wrap gap-x-6 gap-y-2">
        <Legend states={layout.nodes.map((placed) => byName.get(placed.name)?.drift_state ?? "never_scanned")} />
        <HealthKey states={layout.nodes.map((placed) => byName.get(placed.name)?.health?.state)} />
      </div>
    </div>
  );
}
