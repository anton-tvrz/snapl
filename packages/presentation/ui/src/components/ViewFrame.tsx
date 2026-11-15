import { createContext, type ReactNode, useContext, useId } from "react";

import { sourceLabel } from "../api/open";
import type { View } from "../api/types";
import { ageText } from "../lib/time";
import { Command } from "./Command";

/** True when the page shows a history archive: what it does not hold is not a failure (FR-037). */
export const ArchiveMode = createContext(false);

// The server's words for a view an archive does not hold (archive.py NOT_IN_ARCHIVE).
export const NOT_IN_ARCHIVE = "not in this archive";

interface FrameProps {
  title: string;
  /** At most one control, on the right of the header (spec 014 FR-010). */
  action?: ReactNode;
  /** Where the data came from and how old it is. No footer when there is nothing to say. */
  footer?: ReactNode;
  /** For links from the number row. */
  id?: string;
  children: ReactNode;
}

/**
 * The one frame every panel uses (spec 014 FR-010): the title on the left of
 * its header, at most one action on the right, and its source and age in a
 * quiet footer. In the light theme a tinted shadow lifts it from the page; in
 * the dark theme its border does (FR-014).
 */
export function PanelFrame({ title, action, footer, id, children }: FrameProps) {
  const heading = useId();
  return (
    <section
      id={id}
      aria-labelledby={heading}
      className="flex flex-col gap-4 rounded-lg border border-panel-line bg-surface p-6 shadow-panel"
    >
      <header className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <h2 id={heading} className="text-title font-semibold">
          {title}
        </h2>
        {action}
      </header>
      <div className="min-w-0">{children}</div>
      {footer && <footer className="text-meta text-muted">{footer}</footer>}
    </section>
  );
}

interface Props {
  title: string;
  /** Undefined until the first answer arrives. */
  view: View<unknown> | undefined;
  /** The page's "now": the wall clock when live, the archive's time otherwise. */
  now: Date;
  /** True when a past time is shown and this view's source keeps no history. */
  presentOnly?: boolean;
  /** Why the last request failed. Shown in place of "Loading…" before any answer. */
  error?: Error | null;
  /** At most one control, on the right of the header. */
  action?: ReactNode;
  /** For links from the number row. */
  id?: string;
  children: ReactNode;
}

/**
 * One panel. It always says where its data came from and how old it is, and
 * when its source is down it gives the reason and the command that fixes it
 * (010 FR-005, FR-030, FR-031). Stale data is shown, labelled with its age.
 */
export function ViewFrame({ title, view, now, presentOnly = false, error, action, id, children }: Props) {
  const showContent = view !== undefined && (view.available || (view.stale === true && view.data != null));
  const age = ageText(view?.fetched_at, now);
  const archived = useContext(ArchiveMode) && !!view && !view.available && !!view.reason?.startsWith(NOT_IN_ARCHIVE);
  const detail = archived ? view.reason?.slice(NOT_IN_ARCHIVE.length).replace(/^:\s*/, "") : undefined;
  return (
    <PanelFrame
      title={title}
      action={action}
      id={id}
      footer={view !== undefined ? `${sourceLabel(view.source)} · ${age}` : undefined}
    >
      {view === undefined &&
        (error ? (
          <p role="alert" className="max-w-prose text-body break-words">
            {error.message}
          </p>
        ) : (
          <p className="text-body text-muted">Loading…</p>
        ))}
      {archived && (
        <div className="mb-4 text-body text-muted">
          <p role="status" className="font-medium">
            Not in this archive
          </p>
          {detail && <p className="max-w-prose">{detail}</p>}
          {view.remedy && (
            <p className="text-meta">
              To see it live: <code className="font-mono">{view.remedy}</code>
            </p>
          )}
        </div>
      )}
      {view !== undefined && !view.available && !archived && (
        <div className="mb-4 flex flex-col gap-2 rounded border border-warn-line bg-warn-surface p-4 text-body">
          <p role="status" className="font-medium">
            {view.stale ? `Stale: last read ${age}` : "Unavailable"}
          </p>
          {view.reason && <p className="max-w-prose break-words">{view.reason}</p>}
          {view.remedy && (
            <p>
              <Command text={view.remedy} />
            </p>
          )}
        </div>
      )}
      {presentOnly && (
        <p className="mb-4 text-meta text-muted">Shows only the present: this source keeps no history.</p>
      )}
      {showContent && children}
    </PanelFrame>
  );
}
