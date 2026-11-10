import type { DeviceView } from "../../api/types";
import { driftEncoding } from "../../lib/encoding";
import { StateMark } from "./StateMark";

interface Props {
  devices: readonly DeviceView[];
  selected: string | undefined;
  onSelect: (name: string) => void;
}

/**
 * The devices as a list. Shown in place of the drawing when there is no
 * topology to draw: the Source of Truth is down, or there are too many
 * devices for one screen (spec 011 FR-017, FR-018).
 */
export function DeviceList({ devices, selected, onSelect }: Props) {
  if (devices.length === 0) return <p className="text-sm text-muted">No devices recorded yet.</p>;

  return (
    <ul className="grid gap-1.5 sm:grid-cols-2 lg:grid-cols-3">
      {devices.map((device) => {
        const encoding = driftEncoding(device.drift_state);
        const role = device.role ?? "role unknown";
        return (
          <li key={device.device_id}>
            <button
              type="button"
              aria-label={`${device.name}, ${role}, ${encoding.label}`}
              aria-pressed={selected === device.name}
              onClick={() => onSelect(device.name)}
              className="flex w-full items-center gap-2 rounded border border-line bg-surface px-2 py-1.5 text-left text-sm hover:border-ink aria-pressed:border-ink"
            >
              <svg width="20" height="20" viewBox="-11 -11 22 22" aria-hidden="true" className="shrink-0">
                <StateMark encoding={encoding} r={9} />
              </svg>
              <span className="font-mono">{device.name}</span>
              <span className="text-muted">{role}</span>
              <span className="ml-auto text-xs">{encoding.label}</span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
