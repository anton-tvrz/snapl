import { useState } from "react";

import type { RemovalView, RunView, View } from "../../api/types";
import { runEncoding } from "../../lib/encoding";
import { ageText, durationText } from "../../lib/time";
import { ExternalLink } from "../ExternalLink";
import { ViewFrame } from "../ViewFrame";

/** The run's name, linked to its page in the Temporal Web UI in a new tab. */
function RunLink({ run }: { run: RunView }) {
  return (
    <ExternalLink href={run.web_ui_url} title={run.workflow_id}>
      {run.workflow_type}
    </ExternalLink>
  );
}

/** Why a removal wrote nothing, in words (spec 013 FR-016). A reason this page does not know is shown raw. */
const NOTHING_WRITTEN: Record<string, string> = {
  plan_changed: "The device changed after the preview; nothing was removed.",
  deploy_in_flight: "A deploy to the device was running; nothing was removed.",
  remove_failed: "The device rejected the removal; nothing was removed.",
  device_not_found: "The device is not in intent; nothing was removed.",
  intent_unavailable: "Intent could not be read; nothing was removed.",
  collect_failed: "The device could not be read; nothing was removed.",
  cancelled: "Cancelled; nothing was removed.",
};

function PathLine({ label, paths }: { label: string; paths: readonly string[] }) {
  if (paths.length === 0) return null;
  return (
    <p className="font-mono break-all">
      <span className="font-sans">{label}: </span>
      {paths.join(", ")}
    </p>
  );
}

/** What a removal did: who confirmed what, what the one Set deleted, and what the scan after found. Shows, never acts. */
function RemovalDetail({ run, removal }: { run: RunView; removal: RemovalView }) {
  const deleted = removal.deleted_paths ?? [];
  const confirmed = removal.confirmed_paths ?? [];
  const reason = removal.reason;
  const verified = reason === "succeeded";
  return (
    <section aria-label={`Removal ${run.workflow_id}`} className="mt-2 max-w-prose space-y-2 text-meta text-muted">
      <p>
        Confirmed by {removal.actor ?? "an unrecorded operator"}: {confirmed.length} {confirmed.length === 1 ? "path" : "paths"}
      </p>
      {reason == null && <p>Not finished.</p>}
      {deleted.length > 0 && (
        <ul className="space-y-2">
          {deleted.map((path) => (
            <li key={path} className="chip rounded bg-sunken px-2 py-1 font-mono break-all text-ink">
              {path}
            </li>
          ))}
        </ul>
      )}
      {reason != null && deleted.length === 0 && <p>{NOTHING_WRITTEN[reason] ?? `${reason}; nothing was removed.`}</p>}
      <PathLine label="The plan now" paths={removal.plan_paths ?? []} />
      {verified && <p>The verifying scan finds none of them.</p>}
      <PathLine label="Still there" paths={removal.remaining ?? []} />
      <PathLine label="Newly missing or different from intent" paths={removal.new_forward ?? []} />
      <PathLine label="Left in place by design" paths={removal.still_refused ?? []} />
      {removal.device_response && <p className="break-all">Device response: {removal.device_response}</p>}
      {/* The outcome line above already gives the detail's first line; show it only when it says more. */}
      {removal.detail && !(run.reason ?? "").endsWith(removal.detail) && <p className="break-words">{removal.detail}</p>}
    </section>
  );
}

function elapsed(run: RunView, now: Date): string {
  return `${durationText((now.getTime() - Date.parse(run.started_at)) / 1000)} so far`;
}

interface RunsProps {
  runs: View<RunView[]> | undefined;
  /** The time shown: ages and elapsed times are relative to it. */
  now: Date;
  /** The selected device: only its runs are shown until the viewer asks for all. */
  device?: string;
}

/** How many runs the table shows until the viewer asks for all (spec 018 US4, #305). */
const CAP = 10;

/** Recent runs from the audit trail (010 FR-013), running ones first. */
export function Runs({ runs, now, device }: RunsProps) {
  const [all, setAll] = useState(false);
  const [uncapped, setUncapped] = useState(false);
  const list = runs?.data ?? [];
  const filtered = device && !all ? list.filter((run) => run.target === device) : list;
  const running = filtered.filter((run) => run.state === "running");
  const ordered = [...running, ...filtered.filter((run) => run.state !== "running")];
  // Running runs come first and are never hidden, even past the cap.
  const shown = uncapped ? ordered : ordered.slice(0, Math.max(CAP, running.length));

  return (
    <ViewFrame title="Runs" view={runs} now={now}>
      {device && !all && (
        <p className="mb-2 flex flex-wrap items-center gap-2 text-meta text-muted">
          <span>Only {device}'s runs.</span>
          <button type="button" onClick={() => setAll(true)} className="chip rounded border border-line px-2 py-0.5 text-ink">
            Show every device's runs
          </button>
        </p>
      )}
      {list.length === 0 ? (
        <p className="text-body text-muted">No runs recorded yet.</p>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table aria-label="Runs" className="w-full text-left text-body">
              <thead className="text-meta text-muted">
                <tr>
                  <th className="cell py-1 pr-4 font-medium">Started</th>
                  <th className="cell py-1 pr-4 font-medium">Run</th>
                  <th className="cell py-1 pr-4 font-medium">Target</th>
                  <th className="cell py-1 pr-4 font-medium">Outcome</th>
                  <th className="cell py-1 pr-4 font-medium">Trigger</th>
                  <th className="cell py-1 font-medium">Duration</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((run) => {
                  const outcome = runEncoding(run.state);
                  return (
                    <tr key={`${run.workflow_id}@${run.started_at}`} className="border-t border-line align-top">
                      <td className="py-2 pr-4 whitespace-nowrap">{ageText(run.started_at, now)}</td>
                      <td className="py-2 pr-4">
                        <RunLink run={run} />
                      </td>
                      <td className="py-2 pr-4 font-mono text-meta">{run.target ?? ""}</td>
                      <td className="py-2 pr-4">
                        <span aria-hidden="true">{outcome.glyph} </span>
                        {outcome.label}
                        {run.reason && <span className="block max-w-prose text-meta break-words text-muted">{run.reason}</span>}
                        {run.removal && <RemovalDetail run={run} removal={run.removal} />}
                      </td>
                      <td className="py-2 pr-4">
                        {run.trigger}
                        {run.actor ? ` (${run.actor})` : ""}
                      </td>
                      <td className="py-2 whitespace-nowrap">
                        {run.state === "running"
                          ? elapsed(run, now)
                          : run.duration_s == null
                            ? ""
                            : durationText(run.duration_s)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {shown.length < ordered.length && (
            <button
              type="button"
              onClick={() => setUncapped(true)}
              className="chip mt-2 rounded border border-line px-2 py-0.5 text-meta text-ink"
            >
              Show all {ordered.length} runs
            </button>
          )}
        </>
      )}
    </ViewFrame>
  );
}

interface RunningProps {
  running: View<RunView[]> | undefined;
  now: Date;
  presentOnly?: boolean;
}

/** What Temporal says is running now (010 FR-014). */
export function Running({ running, now, presentOnly = false }: RunningProps) {
  const list = running?.data ?? [];
  return (
    <ViewFrame title="Running now" view={running} now={now} presentOnly={presentOnly}>
      {list.length === 0 ? (
        <p className="text-body text-muted">Nothing is running.</p>
      ) : (
        <ul className="space-y-2 text-body">
          {list.map((run) => (
            <li key={`${run.workflow_id}@${run.started_at}`} className="flex flex-wrap gap-x-2">
              <RunLink run={run} />
              {run.target && <span className="font-mono text-meta">{run.target}</span>}
              <span>{elapsed(run, now)}</span>
              <span className="text-muted">{run.trigger}</span>
            </li>
          ))}
        </ul>
      )}
    </ViewFrame>
  );
}
