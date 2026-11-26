// The fabric in two facts, for the space beside the drawing (spec 014 FR-028):
// how many devices in intent are in sync, and which ones differ most.
import type { DeviceView } from "../api/types";

export interface DriftedDevice {
  name: string;
  /** The drift state, raw: drawn with its own shape and glyph. */
  state: string;
  /** Missing, undesired and unclassified paths together. */
  paths: number;
  /** `paths` over the most any device has, for the bar. Null when there is nothing to measure: a scan error, or a state this page does not know. */
  share: number | null;
}

export interface FabricSummary {
  inIntent: number;
  inSync: number;
  /** Devices with any state but never scanned. */
  scanned: number;
  /** Most differing paths first, then by name. */
  drifted: DriftedDevice[];
}

const byName = (a: { name: string }, b: { name: string }) => a.name.localeCompare(b.name, undefined, { numeric: true });

export function fabricSummary(devices: readonly DeviceView[]): FabricSummary {
  const intent = devices.filter((device) => device.in_intent);
  const others = intent
    .filter((device) => device.drift_state !== "in_sync" && device.drift_state !== "never_scanned")
    .map((device) => ({
      name: device.name,
      state: device.drift_state as string,
      paths:
        (device.missing_paths?.length ?? 0) + (device.undesired_paths?.length ?? 0) + (device.unclassified_paths?.length ?? 0),
    }));
  const most = Math.max(0, ...others.map((device) => device.paths));
  const drifted = others
    .map((device) => ({ ...device, share: device.paths > 0 ? device.paths / most : null }))
    .sort((a, b) => b.paths - a.paths || byName(a, b));
  return {
    inIntent: intent.length,
    inSync: intent.filter((device) => device.drift_state === "in_sync").length,
    scanned: intent.filter((device) => device.drift_state !== "never_scanned").length,
    drifted,
  };
}
