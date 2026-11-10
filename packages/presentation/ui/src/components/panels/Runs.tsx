import { useState } from "react";

import type { RunView, View } from "../../api/types";
import { runEncoding } from "../../lib/encoding";
import { ageText, durationText } from "../../lib/time";
import { ViewFrame } from "../ViewFrame";

/** The run's name, linked to its page in the Temporal Web UI in a new tab. */
function RunLink({ run }: { run: RunView }) {
  return (
    <a href={run.web_ui_url} target="_blank" rel="noreferrer" title={run.workflow_id} className="underline underline-offset-2">
      {run.workflow_type}
    </a>
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

/** Recent runs from the audit trail (010 FR-013), running ones first. */
export function Runs({ runs, now, device }: RunsProps) {
  const [all, setAll] = useState(false);
  const list = runs?.data ?? [];
  const filtered = device && !all ? list.filter((run) => run.target === device) : list;
  const ordered = [...filtered.filter((run) => run.state === "running"), ...filtered.filter((run) => run.state !== "running")];

  return (
    <ViewFrame title="Runs" view={runs} now={now}>
      {device && !all && (
        <p className="mb-2 flex flex-wrap items-center gap-2 text-xs text-muted">
          <span>Only {device}'s runs.</span>
          <button type="button" onClick={() => setAll(true)} className="rounded border border-line px-2 py-0.5 text-ink">
            Show every device's runs
          </button>
        </p>
      )}
      {list.length === 0 ? (
        <p className="text-sm text-muted">No runs recorded yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table aria-label="Runs" className="w-full text-left text-sm">
            <thead className="text-xs text-muted">
              <tr>
                <th className="py-1 pr-3 font-medium">Started</th>
                <th className="py-1 pr-3 font-medium">Run</th>
                <th className="py-1 pr-3 font-medium">Target</th>
                <th className="py-1 pr-3 font-medium">Outcome</th>
                <th className="py-1 pr-3 font-medium">Trigger</th>
                <th className="py-1 font-medium">Duration</th>
              </tr>
            </thead>
            <tbody>
              {ordered.map((run) => {
                const outcome = runEncoding(run.state);
                return (
                  <tr key={`${run.workflow_id}@${run.started_at}`} className="border-t border-line align-top">
                    <td className="py-1 pr-3 whitespace-nowrap">{ageText(run.started_at, now)}</td>
                    <td className="py-1 pr-3">
                      <RunLink run={run} />
                    </td>
                    <td className="py-1 pr-3 font-mono text-xs">{run.target ?? ""}</td>
                    <td className="py-1 pr-3">
                      <span aria-hidden="true">{outcome.glyph} </span>
                      {outcome.label}
                      {run.reason && <span className="block text-xs break-words text-muted">{run.reason}</span>}
                    </td>
                    <td className="py-1 pr-3">
                      {run.trigger}
                      {run.actor ? ` (${run.actor})` : ""}
                    </td>
                    <td className="py-1 whitespace-nowrap">
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
        <p className="text-sm text-muted">Nothing is running.</p>
      ) : (
        <ul className="space-y-1 text-sm">
          {list.map((run) => (
            <li key={`${run.workflow_id}@${run.started_at}`} className="flex flex-wrap gap-x-2">
              <RunLink run={run} />
              {run.target && <span className="font-mono text-xs">{run.target}</span>}
              <span>{elapsed(run, now)}</span>
              <span className="text-muted">{run.trigger}</span>
            </li>
          ))}
        </ul>
      )}
    </ViewFrame>
  );
}
