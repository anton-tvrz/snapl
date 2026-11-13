import type { KeyboardEvent } from "react";

import type { DeviceView, Topology as TopologyData } from "../../api/types";
import type { Activity, FabricActivity } from "../../lib/activity";
import { driftEncoding, runEncoding } from "../../lib/encoding";
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
        <p className="mb-3 text-sm text-muted">
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
        <p role="status" className="mb-2 text-xs text-muted">
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
          const label = [
            placed.name,
            placed.role,
            encoding.label,
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
      <div className="mt-2">
        <Legend states={layout.nodes.map((placed) => byName.get(placed.name)?.drift_state ?? "never_scanned")} />
      </div>
    </div>
  );
}
