import type { ModeResponse } from "../api/types";
import { utcText } from "../lib/time";

/**
 * Which archive the page shows (spec 011 FR-035): its name, when it was made,
 * the commit, and the parts it lacks. Nothing in live mode.
 */
export function ModeBanner({ mode }: { mode: ModeResponse | undefined }) {
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
      <p className="mt-1">Nothing is contacted. Ages are relative to when it was made.</p>
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
