import type { DriftTimeline } from "../../api/types";
import { headline } from "../../lib/episodes";

interface Props {
  /** A timeline answered as of `asOf`. */
  timeline: DriftTimeline;
  asOf: Date;
}

/**
 * The latest repaired drift (spec 011 FR-025): how long it lasted, scan to
 * scan, and what repaired it, with the drift still open beside it.
 */
export function Headline({ timeline, asOf }: Props) {
  const text = headline(timeline, asOf);
  const recorded = text.latest !== null || timeline.episodes.length > 0 || timeline.open_count > 0;

  const { count, devices, others } = text.open;

  return (
    <div className="mb-4 flex flex-wrap items-start gap-x-6 gap-y-2">
      {text.latest ? (
        <>
          <p className="text-number font-semibold tabular-nums">{text.latest.duration}</p>
          <p className="max-w-prose min-w-0 flex-1 text-body">
            <span className="font-medium">Latest repaired drift.</span> {text.latest.sentence}
          </p>
        </>
      ) : (
        <p className="text-body font-medium">
          {recorded ? "No drift has been repaired yet." : "No drift has been recorded."}
        </p>
      )}
      <div className="flex basis-full flex-wrap items-center gap-x-4 gap-y-2 text-body text-muted">
        <p>{count === 0 ? "No drift open." : `${count} ${count === 1 ? "drift" : "drifts"} open`}</p>
        {devices.length > 0 && (
          <ul aria-label="Devices with open drift" className="flex flex-wrap gap-2">
            {devices.map((device) => (
              <li key={device.name} className="chip rounded-full border border-line px-2 py-0.5 text-meta text-ink">
                <span className="font-mono">{device.name}</span> for {device.duration}
              </li>
            ))}
          </ul>
        )}
        {others > 0 && (
          <p>
            {others} more on {others === 1 ? "a device" : "devices"} no longer in intent.
          </p>
        )}
      </div>
    </div>
  );
}
