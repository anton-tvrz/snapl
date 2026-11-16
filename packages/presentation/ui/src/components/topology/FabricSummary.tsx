import { driftEncoding, TONES } from "../../lib/encoding";
import type { FabricSummary as Summary } from "../../lib/summary";
import { StateMark } from "./StateMark";

interface Props {
  summary: Summary;
  onSelect: (name: string) => void;
}

const R = 46;
const WIDTH = 12;
const LENGTH = 2 * Math.PI * R;

/** The share in sync as a ring. The count in words carries the meaning; the arc only shows it. */
function Ring({ summary }: { summary: Summary }) {
  const unscanned = summary.scanned === 0;
  const share = unscanned ? 0 : summary.inSync / summary.inIntent;
  const label = unscanned ? "No device has been scanned yet" : `${summary.inSync} of ${summary.inIntent} devices in sync`;
  return (
    <div role="img" aria-label={label} className="relative mx-auto h-40 w-40">
      <svg viewBox="0 0 120 120" className="h-full w-full" aria-hidden="true">
        <g transform="translate(60 60) rotate(-90)">
          {/* With no scan the ring is the dashed "never scanned" ring, and nothing is filled. */}
          <circle
            data-track=""
            r={R}
            fill="none"
            stroke={unscanned ? "var(--ink)" : "var(--line)"}
            strokeWidth={unscanned ? 1.5 : WIDTH}
            {...(unscanned && { strokeDasharray: "3 3" })}
          />
          {share > 0 && (
            <>
              <circle
                data-arc=""
                data-share={share.toFixed(3)}
                r={R}
                fill="none"
                stroke={TONES.ok.fill}
                strokeWidth={WIDTH}
                strokeDasharray={`${share * LENGTH} ${LENGTH}`}
              />
              {/* An ink outline on both edges, so the arc reads where its fill is close to the surface. */}
              {[R - WIDTH / 2, R + WIDTH / 2].map((r) => (
                <circle
                  key={r}
                  r={r}
                  fill="none"
                  stroke="var(--ink)"
                  strokeWidth={1}
                  strokeDasharray={`${share * 2 * Math.PI * r} ${2 * Math.PI * r}`}
                />
              ))}
            </>
          )}
        </g>
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center text-center">
        {unscanned ? (
          <p className="text-body text-muted">No scan yet.</p>
        ) : (
          <>
            <p className="text-number font-semibold tabular-nums">
              {summary.inSync} of {summary.inIntent}
            </p>
            <p className="text-meta text-muted">in sync</p>
          </>
        )}
      </div>
    </div>
  );
}

/**
 * The fabric's own summary, beside the drawing when no device is selected
 * (spec 014 US5): the share of devices in sync as a ring with the count in
 * words, and the devices that differ, ranked, each with its state's mark.
 */
export function FabricSummary({ summary, onSelect }: Props) {
  if (summary.inIntent === 0) return <p className="text-body text-muted">No device is in intent.</p>;

  return (
    <div className="flex flex-col gap-6">
      <Ring summary={summary} />
      {summary.drifted.length === 0 ? (
        summary.scanned > 0 && <p className="text-center text-body text-muted">No drift open.</p>
      ) : (
        <ul aria-label="Drifted devices, most paths first" className="flex flex-col gap-2">
          {summary.drifted.map((device) => {
            const encoding = driftEncoding(device.state);
            const count =
              device.share === null ? "" : `, ${device.paths} ${device.paths === 1 ? "path differs" : "paths differ"}`;
            return (
              <li key={device.name}>
                <button
                  type="button"
                  // Not "<name>, …": that is how the drawing names its devices.
                  aria-label={`Open ${device.name}: ${encoding.label}${count}`}
                  onClick={() => onSelect(device.name)}
                  className="grid w-full grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-2 gap-y-2 rounded border border-line p-2 text-left text-body hover:border-ink"
                >
                  <svg width="20" height="20" viewBox="-11 -11 22 22" aria-hidden="true" data-shape={encoding.shape} className="shrink-0">
                    <StateMark encoding={encoding} r={9} />
                  </svg>
                  <span className="truncate">
                    <span className="font-mono">{device.name}</span> <span className="text-meta text-muted">{encoding.label}</span>
                  </span>
                  {device.share !== null && <span className="text-meta tabular-nums">{device.paths}</span>}
                  {device.share !== null && (
                    <span aria-hidden="true" className="col-span-3 h-2 rounded-full bg-sunken">
                      {/* Ink, not a state colour: the mark beside it says which state. */}
                      <span data-bar="" className="block h-2 rounded-full bg-line-strong" style={{ width: `${device.share * 100}%` }} />
                    </span>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
