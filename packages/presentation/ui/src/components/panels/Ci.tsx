import type { CiStatus, View } from "../../api/types";
import { jobEncoding } from "../../lib/encoding";
import { Command } from "../Command";
import { ExternalLink } from "../ExternalLink";
import { ViewFrame } from "../ViewFrame";

// The same remedy the server gives when it cannot read CI at all (ci.py TOKEN_REMEDY).
const TOKEN_REMEDY = "export GITHUB_TOKEN=…";

interface Props {
  ci: View<CiStatus> | undefined;
  now: Date;
  presentOnly?: boolean;
}

/** CI on main and the open PRs (010 FR-020): each gate, the failed jobs, main's coverage. */
export function Ci({ ci, now, presentOnly = false }: Props) {
  const status = ci?.data;
  const targets = [...(status?.targets ?? [])].sort((a, b) => Number(b.kind === "main") - Number(a.kind === "main"));
  return (
    <ViewFrame title="CI" view={ci} now={now} presentOnly={presentOnly}>
      {status && (
        <div className="text-sm">
          <p className="mb-2 text-xs text-muted">{status.repo}</p>
          <div className="overflow-x-auto">
            <table aria-label="CI" className="w-full text-left">
              <thead className="text-xs text-muted">
                <tr>
                  <th className="py-1 pr-3 font-medium">Target</th>
                  <th className="py-1 pr-3 font-medium">Gate</th>
                  <th className="py-1 pr-3 font-medium">Failed jobs</th>
                  <th className="py-1 font-medium">Coverage</th>
                </tr>
              </thead>
              <tbody>
                {targets.map((target) => {
                  const label = target.kind === "main" ? "main" : `#${target.number ?? "?"} ${target.title ?? ""}`.trim();
                  const gate = jobEncoding(target.gate);
                  const failed = target.jobs.filter((job) => job.result === "failure");
                  return (
                    <tr key={`${target.kind}-${target.number ?? target.head_sha}`} className="border-t border-line align-top">
                      <td className="py-1 pr-3">
                        {target.run_url ? <ExternalLink href={target.run_url}>{label}</ExternalLink> : label}
                      </td>
                      <td className="py-1 pr-3 whitespace-nowrap">
                        <span aria-hidden="true">{gate.glyph} </span>
                        {gate.label}
                      </td>
                      <td className="py-1 pr-3">
                        {failed.map((job) => (
                          <span key={job.url} className="mr-2 inline-block">
                            <ExternalLink href={job.url}>{job.name}</ExternalLink>
                          </span>
                        ))}
                      </td>
                      <td className="py-1 whitespace-nowrap">
                        {target.kind === "main" &&
                          (target.coverage_pct == null ? "none reported" : `${target.coverage_pct.toFixed(1)}%`)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {(status.omitted_prs ?? 0) > 0 && (
            <div className="mt-3">
              <p>{status.omitted_prs} more PRs were not checked without a token.</p>
              <p className="mt-1">
                <Command text={TOKEN_REMEDY} />
              </p>
            </div>
          )}
        </div>
      )}
    </ViewFrame>
  );
}
