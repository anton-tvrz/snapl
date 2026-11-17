import { type ReactNode, useId } from "react";

import { GroupLabel } from "./GroupLabel";

interface Props {
  /** The anchor: a closed fold carries it, an open one leaves it to its content. */
  id: string;
  title: string;
  open: boolean;
  onToggle: (id: string) => void;
  /** A group of this many panels, under a third-level label. Unset: one panel. */
  group?: number;
  children: ReactNode;
}

const toggle = "text-meta text-muted underline underline-offset-2 hover:text-ink";

/**
 * A panel or a group of panels shown as its heading until it is opened, so the
 * page stays short enough for the metrics to start near the top (spec 014
 * SC-003, #290). Its content is not rendered while closed, so it asks nothing.
 */
export function Fold({ id, title, open, onToggle, group, children }: Props) {
  const heading = useId();
  const button = (
    <button
      type="button"
      aria-expanded={open}
      aria-controls={id}
      aria-label={`${open ? "Hide" : "Show"} ${title}`}
      onClick={() => onToggle(id)}
      className={toggle}
    >
      {open ? "Hide" : group === undefined ? "Show" : `Show ${group} ${group === 1 ? "panel" : "panels"}`}
    </button>
  );

  if (group !== undefined) {
    return (
      <div id={id} role="group" aria-label={title}>
        <div className="flex items-baseline justify-between gap-4">
          <GroupLabel level={3}>{title}</GroupLabel>
          {button}
        </div>
        {open && children}
      </div>
    );
  }

  if (open) {
    return (
      <div className="flex flex-col gap-2">
        <div className="flex justify-end">{button}</div>
        {children}
      </div>
    );
  }
  return (
    <section
      id={id}
      aria-labelledby={heading}
      className="flex items-center justify-between gap-4 rounded-lg border border-panel-line bg-surface px-6 py-4 shadow-panel"
    >
      <h2 id={heading} className="text-title font-semibold">
        {title}
      </h2>
      {button}
    </section>
  );
}
