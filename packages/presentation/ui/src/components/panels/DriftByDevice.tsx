import type { DeviceDriftSummary, DriftSummary, View } from "../../api/types";
import { durationText } from "../../lib/time";
import { ViewFrame } from "../ViewFrame";

interface Props {
  /** The `summary` view of `/api/v1/drift-summary`. Undefined until the first answer arrives. */
  summary: View<DriftSummary> | undefined;
  now: Date;
  error?: Error | null;
  onSelect: (name: string) => void;
  id?: string;
}

const plural = (count: number, one: string, many: string) => `${count} ${count === 1 ? one : many}`;

/** How long a device drifted inside the range, scan to scan. */
function drifted(device: DeviceDriftSummary): string {
  // An open drift seen by one scan has no second scan to measure to (spec 015 PR B).
  return device.drifted_s === 0 ? "seen by one scan so far" : `drifted ${durationText(device.drifted_s)}`;
}

/** The most paths any scan in the range saw, by direction. */
function paths(device: DeviceDriftSummary): string {
  const missing = `at most ${plural(device.most_missing, "path", "paths")} missing`;
  // Scans before the undesired split (010 FR-012a) did not record it: unknown, not 0.
  return device.most_undesired === null || device.most_undesired === undefined
    ? `${missing}; undesired not recorded`
    : `${missing} and ${device.most_undesired} undesired`;
}

/** The row's facts in one sentence: what a screen reader hears and what the row shows. */
function describe(device: DeviceDriftSummary): string {
  const open = device.open > 0 ? `, ${device.open} still open` : "";
  return `${plural(device.episodes, "drift", "drifts")}${open}, ${drifted(device)}, ${paths(device)}`;
}

function Row({ device, longest, onSelect }: { device: DeviceDriftSummary; longest: number; onSelect: (name: string) => void }) {
  const share = longest > 0 ? device.drifted_s / longest : 0;
  const body = (
    <>
      <span className="truncate">
        <span className="font-mono">{device.name}</span>{" "}
        <span className="text-meta text-muted">
          {plural(device.episodes, "drift", "drifts")}
          {device.open > 0 && `, ${device.open} still open`}
          {!device.in_intent && ", "}
          {!device.in_intent && <span>no longer in intent</span>}
        </span>
      </span>
      <span className="text-meta tabular-nums">{device.drifted_s === 0 ? "one scan" : durationText(device.drifted_s)}</span>
      <span aria-hidden="true" className="col-span-2 h-2 rounded-full bg-sunken">
        {/* Ink, not a state colour: the length is time, not a state. */}
        <span data-bar="" className="block h-2 rounded-full bg-line-strong" style={{ width: `${share * 100}%` }} />
      </span>
      <span className="col-span-2 text-meta text-muted">{paths(device)}</span>
    </>
  );
  const layout = "grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-x-2 gap-y-2 rounded border border-line p-2 text-left text-body";
  if (!device.in_intent) return <div className={layout}>{body}</div>;
  return (
    <button
      type="button"
      aria-label={`Open ${device.name}: ${describe(device)}`}
      onClick={() => onSelect(device.name)}
      className={`${layout} hover:border-ink`}
    >
      {body}
    </button>
  );
}

/**
 * Drift by device over the page's range (spec 015 FR-006, US2): each device
 * that drifted, as the server ranks them, with a bar of its time drifted scan
 * to scan, its drifts and how many are still open, and the most paths any
 * scan saw. The words carry every fact; the bar only shows the time.
 */
export function DriftByDevice({ summary, now, error, onSelect, id }: Props) {
  const devices = summary?.data?.devices ?? [];
  const longest = Math.max(0, ...devices.map((device) => device.drifted_s));
  return (
    <ViewFrame title="Drift by device" view={summary} now={now} error={error} id={id}>
      {devices.length === 0 ? (
        <p className="text-body text-muted">No drift in this range.</p>
      ) : (
        <ul aria-label="Devices that drifted, most drifts first" className="flex flex-col gap-2">
          {devices.map((device) => (
            <li key={device.device_id}>
              <Row device={device} longest={longest} onSelect={onSelect} />
            </li>
          ))}
        </ul>
      )}
    </ViewFrame>
  );
}
