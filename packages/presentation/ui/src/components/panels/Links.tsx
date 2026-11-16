import type { LinksResponse } from "../../api/types";
import { Command } from "../Command";
import { ExternalLink } from "../ExternalLink";

const GROUPS: Record<string, string> = { stack: "Stack", github: "GitHub", api: "API", docs: "Docs" };

/**
 * Where to go next (010 FR-021): the stack, the repository, this API and the
 * docs. A link on this dashboard opens in place; any other opens a new tab.
 */
export function Links({ links }: { links: LinksResponse | undefined }) {
  if (!links) return null;
  const groups = new Map<string, LinksResponse["links"]>();
  for (const link of links.links) groups.set(link.group, [...(groups.get(link.group) ?? []), link]);

  return (
    <section aria-label="Links" className="text-body">
      <h2 className="mb-4 text-meta font-semibold tracking-widest text-muted uppercase">Links</h2>
      <div className="flex flex-wrap gap-x-8 gap-y-4">
        {[...groups].map(([group, items]) => (
          <div key={group} role="group" aria-label={GROUPS[group] ?? group}>
            <p className="text-meta font-semibold text-muted">{GROUPS[group] ?? group}</p>
            <ul className="mt-2 space-y-2">
              {items.map((item) => (
                <li key={item.url}>
                  {item.url.startsWith("/") ? (
                    <a href={item.url} className="underline underline-offset-2">
                      {item.label}
                    </a>
                  ) : (
                    <ExternalLink href={item.url}>{item.label}</ExternalLink>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
      {links.reason && (
        <div className="mt-4">
          <p>{links.reason}</p>
          {links.remedy && (
            <p className="mt-2">
              <Command text={links.remedy} />
            </p>
          )}
        </div>
      )}
    </section>
  );
}
