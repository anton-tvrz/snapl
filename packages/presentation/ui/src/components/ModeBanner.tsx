import type { ModeResponse } from "../api/types";
import { utcText } from "../lib/time";

/**
 * Which archive the page shows (spec 011 FR-035): its name, when it was made,
 * the commit, and the parts it lacks. Nothing in live mode.
 */
export function ModeBanner({ mode }: { mode: ModeResponse | undefined }) {
  if (mode?.mode === "snapshot" && mode.archive) return <RecordingBanner info={mode.archive} />;
  const archive = mode?.mode === "archive" ? mode.archive : null;
  if (!archive) return null;
  const missing = archive.parts.filter((part) => !part.present);
  const commit = archive.snapl_commit ? `commit ${archive.snapl_commit.slice(0, 7)}` : "an unknown commit";

  return (
    <section aria-label="Archive" className="mb-4 rounded-lg border-2 border-ink bg-sunken p-3 text-sm">
      <p className="font-semibold">
        Archive <span className="font-mono">{archive.name}</span>, made {utcText(new Date(archive.created_at))} from{" "}
        {commit}.
      </p>
      <p className="mt-1">This page contacts nothing. Ages count from when the archive was made.</p>
      {missing.length > 0 && (
        <ul aria-label="Missing from this archive" className="mt-2 space-y-0.5 text-xs">
          {missing.map((part) => (
            <li key={part.name}>
              {part.name}: {part.reason ?? "no reason recorded"}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** A snapshot file (spec 011 FR-041): a recording, never live. */
function RecordingBanner({ info }: { info: NonNullable<ModeResponse["archive"]> }) {
  const commit = info.snapl_commit ? `commit ${info.snapl_commit.slice(0, 7)}` : "an unknown commit";
  const range =
    info.metrics_start && info.metrics_end
      ? `, covering ${utcText(new Date(info.metrics_start))} to ${utcText(new Date(info.metrics_end))}`
      : "";
  return (
    <section aria-label="Recording" className="mb-4 rounded-lg border-2 border-ink bg-sunken p-3 text-sm">
      <p className="font-semibold">
        A recording made {utcText(new Date(info.created_at))} from {commit}
        {range}.
      </p>
      <p className="mt-1">Nothing here is live, and nothing refreshes.</p>
    </section>
  );
}
