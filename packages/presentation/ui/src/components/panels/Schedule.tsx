import type { ScheduleStatus, View, WorkerStatus } from "../../api/types";
import { ageText, durationText, utcText } from "../../lib/time";
import { Command } from "../Command";
import { ViewFrame } from "../ViewFrame";

interface Props {
  schedule: View<ScheduleStatus> | undefined;
  worker: View<WorkerStatus> | undefined;
  now: Date;
  /** True at a past time: Temporal keeps no history. */
  presentOnly?: boolean;
}

const MODES: Record<string, string> = { remediate: "Remediate", report: "Report" };

function Current({ status, now }: { status: ScheduleStatus; now: Date }) {
  const s = status.schedule;
  if (!status.exists || !s) {
    return (
      <div>
        <p>No drift schedule.</p>
        {status.suggested_command && (
          <p className="mt-2">
            <Command text={status.suggested_command} />
          </p>
        )}
      </div>
    );
  }
  const next = s.paused ? undefined : s.next_runs?.[0];
  const last = s.recent_runs?.at(-1);
  const summary = last?.summary;
  return (
    <div className="space-y-1">
      <p className="font-medium">
        {MODES[s.mode] ?? s.mode} every {durationText(s.interval)} · {s.paused ? "paused" : "active"}
      </p>
      {s.paused && (
        <p>
          Paused{status.paused_by ? ` by ${status.paused_by}` : ""}
          {status.paused_at ? ` ${ageText(status.paused_at, now)}` : ""}
          {s.note ? `: ${s.note}` : ""}
        </p>
      )}
      {next && (
        <p>
          Next run in {durationText((Date.parse(next) - now.getTime()) / 1000)} ({utcText(new Date(next))})
        </p>
      )}
      {last && (
        <p>
          Last run {ageText(last.started_at, now)}: {last.status.toLowerCase()}
          {summary &&
            `, ${summary.total} devices scanned, ${summary.drifted} drifted, ${summary.remediated} repaired`}
        </p>
      )}
    </div>
  );
}

function Worker({ worker }: { worker: View<WorkerStatus> | undefined }) {
  const data = worker?.data;
  if (!data) return <p className="text-muted">Worker state unknown.</p>;
  if (data.pollers === 0) {
    return (
      <div>
        <p>No worker is polling {data.task_queue}.</p>
        <p className="mt-2">
          <Command text="uv run invoke orchestrator.start" />
        </p>
      </div>
    );
  }
  return (
    <p>
      {data.pollers} {data.pollers === 1 ? "worker" : "workers"} polling {data.task_queue}
    </p>
  );
}

/**
 * The drift schedule and the worker (010 FR-016 to FR-018). Only a current
 * answer from Temporal can say whether a schedule exists; repeated failed
 * repairs come from the audit trail and are shown even when it is stale.
 */
export function Schedule({ schedule, worker, now, presentOnly = false }: Props) {
  const status = schedule?.data;
  return (
    <ViewFrame title="Schedule and worker" view={schedule} now={now} presentOnly={presentOnly}>
      <div className="space-y-3 text-sm">
        {status && schedule?.available && <Current status={status} now={now} />}
        {(status?.repeated_failures ?? []).map((failure) => (
          <div key={failure.device_id} className="rounded border border-warn-line bg-warn-surface p-2">
            <p>
              {failure.name}: {failure.consecutive_failures} scheduled repairs failed in a row. Stop repairing with:
            </p>
            <p className="mt-1">
              <Command text={failure.suggested_command} />
            </p>
          </div>
        ))}
        <Worker worker={worker} />
      </div>
    </ViewFrame>
  );
}
