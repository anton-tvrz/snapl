import type { LabEvidence as Evidence, LabRun, View } from "../../api/types";
import { labEncoding } from "../../lib/encoding";
import { ageText } from "../../lib/time";
import { Command } from "../Command";
import { ViewFrame } from "../ViewFrame";

interface Props {
  lab: View<Evidence> | undefined;
  now: Date;
  presentOnly?: boolean;
}

function result(run: LabRun): string {
  const status = labEncoding(run.status).label;
  if (run.passed == null) return status;
  return `${status}: ${run.passed} passed, ${run.failed ?? 0} failed, ${run.skipped ?? 0} skipped`;
}

function where(run: LabRun, now: Date): string {
  const parts: string[] = [];
  if (run.commit) parts.push(`at ${run.commit.slice(0, 8)}${run.branch ? ` on ${run.branch}` : ""}`);
  if (run.dirty) parts.push("with uncommitted changes");
  const when = run.started_at ? ageText(run.started_at, now) : run.id;
  return [parts.join(" "), when].filter(Boolean).join(" · ");
}

/**
 * The live lab evidence (010 FR-019): the latest recorded run of each suite,
 * and how far main has moved since it.
 */
export function LabEvidence({ lab, now, presentOnly = false }: Props) {
  const evidence = lab?.data;
  const latest = Object.entries(evidence?.latest ?? {}) as [string, LabRun][];
  const odd = (evidence?.runs ?? []).filter((run) => run.status === "incomplete" || run.status === "unreadable");

  return (
    <ViewFrame title="Live lab evidence" view={lab} now={now} presentOnly={presentOnly}>
      <div className="space-y-3 text-sm">
        {latest.length === 0 ? (
          <div>
            <p>No live lab run recorded yet. Record one with:</p>
            <p className="mt-1">
              <Command text="uv run invoke test-e2e" />
            </p>
          </div>
        ) : (
          latest.map(([suite, run]) => {
            const since = evidence?.commits_on_main_since[suite];
            const encoding = labEncoding(run.status);
            return (
              <div key={suite} role="group" aria-label={suite}>
                <p>
                  <span className="font-semibold">{suite}</span>{" "}
                  <span aria-hidden="true">{encoding.glyph} </span>
                  {result(run)}
                </p>
                <p className="text-xs text-muted">{where(run, now)}</p>
                <p className="text-xs">
                  {since == null
                    ? `Commits on main since this run: unknown (${evidence?.commits_unknown_reason?.[suite] ?? "no reason given"})`
                    : `${since} commits on main since this run (matched by ${evidence?.matched_by?.[suite] ?? "unknown"})`}
                </p>
              </div>
            );
          })
        )}
        {odd.length > 0 && (
          <ul className="space-y-1 text-xs text-muted">
            {odd.map((run) => (
              <li key={run.id}>
                {run.id}: {labEncoding(run.status).label}
                {run.problem ? ` (${run.problem})` : ""}
              </li>
            ))}
          </ul>
        )}
      </div>
    </ViewFrame>
  );
}
