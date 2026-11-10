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

  return (
    <div className="mb-4 flex flex-wrap items-start gap-x-6 gap-y-2">
      {text.latest ? (
        <>
          <p className="text-3xl font-semibold tabular-nums">{text.latest.duration}</p>
          <p className="max-w-prose min-w-0 flex-1 text-sm">
            <span className="font-medium">Latest repaired drift.</span> {text.latest.sentence}
          </p>
        </>
      ) : (
        <p className="text-sm font-medium">
          {recorded ? "No drift has been repaired yet." : "No drift has been recorded."}
        </p>
      )}
      <p className="basis-full text-sm text-muted">{text.open}</p>
    </div>
  );
}
