import type { ReactNode } from "react";

interface Props {
  /** 2 for a group of panels on the page, 3 for a group inside a section. */
  level?: 2 | 3;
  children: ReactNode;
}

/** The small label above a group of panels (spec 014 FR-016). */
export function GroupLabel({ level = 2, children }: Props) {
  const Tag = level === 2 ? "h2" : "h3";
  return <Tag className="mb-4 text-meta font-semibold tracking-widest text-muted uppercase">{children}</Tag>;
}
