// Which devices have work in progress, or work that just finished (spec 011
// FR-013, research R9). The captured deploys took about a second, so a mark
// fed only by "what is running now" at a 5 s refresh would never show them.
// A run that ended within the last RECENT_SECONDS is shown too.

/** Two refreshes: a run that ended between polls is always seen once. */
export const RECENT_SECONDS = 10;

interface Run {
  workflow_type: string;
  target?: string | null;
  state: string;
  ended_at?: string | null;
}

export interface Activity {
  kind: "active" | "recent";
  workflowType: string;
  /** How a recent run ended. Unset while a run is active. */
  outcome?: string;
}

export interface FabricActivity {
  devices: Map<string, Activity>;
  /** A run that targets the whole use case marks the fabric, not each device. */
  fabric: Activity | null;
}

interface Input {
  /** What the workflow engine says is running now. */
  running: readonly Run[] | null | undefined;
  /** Recent runs from the audit trail. */
  runs: readonly Run[] | null | undefined;
  /** The response's own time: ages are never taken from the browser's clock. */
  now: string;
  /** The names of the devices drawn. Any other target is fabric-wide. */
  devices: ReadonlySet<string>;
}

export function activityFor({ running, runs, now, devices }: Input): FabricActivity {
  const result: FabricActivity = { devices: new Map(), fabric: null };
  const mark = (run: Run, activity: Activity) => {
    if (run.target && devices.has(run.target)) result.devices.set(run.target, activity);
    else result.fabric = activity;
  };

  const cutoff = new Date(now).getTime() - RECENT_SECONDS * 1000;
  const recent = (runs ?? [])
    .filter((run) => run.ended_at && new Date(run.ended_at).getTime() >= cutoff)
    .sort((a, b) => new Date(a.ended_at!).getTime() - new Date(b.ended_at!).getTime());
  // Oldest first, so the latest run of a device is the one kept.
  for (const run of recent) mark(run, { kind: "recent", workflowType: run.workflow_type, outcome: run.state });
  // Running last, so it wins over a run that just ended on the same device.
  for (const run of running ?? []) mark(run, { kind: "active", workflowType: run.workflow_type });
  return result;
}
