import type { HistoryResponse } from "../../api/types";
import { type Counts, historyRows, markerText } from "../../lib/history";
import { ageText, utcText } from "../../lib/time";

interface Props {
  history: HistoryResponse | undefined;
  now: Date;
  error?: Error | null;
}

const BUCKET_WORDS: Record<string, string> = { "1h": "hour", "1d": "day", "1w": "week", "5m": "5 minutes" };

const pair = (c: Counts) => (c.succeeded === null ? "" : `${c.succeeded} / ${c.failed ?? 0}`);

/**
 * 010's history (FR-022 there): per bucket, the scans, the most drifted
 * devices and undesired paths any scan saw, and the deploys and repairs, from
 * the audit trail. A bucket with no scan and no deploy says so.
 */
export function History({ history, now, error }: Props) {
  const deploys = history?.series.find((series) => series.name === "deploys");
  const word = BUCKET_WORDS[deploys?.bucket ?? "1h"] ?? "bucket";
  const title = `Drift and deploys by ${word}`;
  const rows = history ? historyRows(history) : [];
  const markers = deploys?.markers ?? [];

  return (
    <section aria-label={title} className="rounded-lg border border-line bg-surface p-4 text-sm">
      <header className="mb-3 flex flex-wrap items-baseline justify-between gap-x-4">
        <h2 className="text-sm font-semibold">{title}</h2>
        <p className="text-xs text-muted">Audit trail</p>
      </header>
      {error && (
        <p role="alert" className="mb-2 break-words">
          History unavailable: {error.message}
        </p>
      )}
      {!history && !error && <p className="text-muted">Loading…</p>}
      {history && (
        <>
          <p className="mb-2 text-xs text-muted">
            {deploys?.first_event_at
              ? `The audit trail starts ${ageText(deploys.first_event_at, now)}.`
              : "No audit events read."}
          </p>
          <div className="max-h-80 overflow-auto">
            <table aria-label={title} className="w-full text-left text-xs">
              <thead className="sticky top-0 bg-surface text-muted">
                <tr>
                  <th className="py-1 pr-3 font-medium">Start (UTC)</th>
                  <th className="py-1 pr-3 font-medium">Scans</th>
                  <th className="py-1 pr-3 font-medium">Most drifted devices</th>
                  <th className="py-1 pr-3 font-medium">Most undesired paths</th>
                  <th className="py-1 pr-3 font-medium">Deploys ok / failed</th>
                  <th className="py-1 font-medium">Repairs ok / failed</th>
                </tr>
              </thead>
              <tbody className="tabular-nums">
                {rows.map((row) => (
                  <tr key={row.start} className="border-t border-line">
                    <td className="py-0.5 pr-3 whitespace-nowrap">{utcText(new Date(row.start)).slice(0, 16)}</td>
                    {row.quiet ? (
                      <td colSpan={5} className="py-0.5 text-muted">
                        no scans
                      </td>
                    ) : (
                      <>
                        <td className="py-0.5 pr-3">{row.scans}</td>
                        <td className="py-0.5 pr-3">{row.mostDrifted ?? ""}</td>
                        <td className="py-0.5 pr-3">{row.mostUndesired ?? ""}</td>
                        <td className="py-0.5 pr-3">{pair(row.deploys)}</td>
                        <td className="py-0.5">{pair(row.repairs)}</td>
                      </>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {markers.length > 0 && (
            <ul aria-label="Repair runs in this range" className="mt-2 space-y-0.5 text-xs text-muted">
              {markers.map((marker) => (
                <li key={`${marker.workflow_id}@${marker.t}`}>{markerText(marker, now)}</li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
