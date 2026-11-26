import type { ReactNode } from "react";

/** A link to another site: a new tab, sending no referrer (spec 011 FR-005). */
export function ExternalLink({ href, children, title }: { href: string; children: ReactNode; title?: string }) {
  return (
    <a href={href} target="_blank" rel="noreferrer" title={title} className="underline underline-offset-2">
      {children}
    </a>
  );
}
