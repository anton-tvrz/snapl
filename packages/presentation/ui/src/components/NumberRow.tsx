import type { ReactNode } from "react";

import type { NumberCard, NumberCardId } from "../lib/numbers";

interface Props {
  cards: readonly NumberCard[];
}

// Line icons, drawn inline: nothing is loaded, so they are in the snapshot too.
// They only decorate. The label, the value and the context carry the meaning.
const ICONS: Record<NumberCardId, ReactNode> = {
  // Three devices and their links.
  in_sync: <path d="M12 4v5m0 0-6 6m6-6 6 6M10 3h4v2h-4zM4 15h4v4H4zm12 0h4v4h-4z" />,
  // Not equal.
  open_drifts: <path d="M5 9h14M5 15h14M15 4 9 20" />,
  // A clock.
  latest_repair: <path d="M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zm0-14v5l3 2" />,
  // Round again.
  runs: <path d="M4 12a8 8 0 0 1 13.7-5.7L20 8m0-4v4h-4m4 4a8 8 0 0 1-13.7 5.7L4 16m0 4v-4h4" />,
};

/**
 * The fabric in four numbers (spec 014 US1). Each card says what it counts,
 * shows one large value and a line of context, and links to the panel with
 * the detail. A card with no value says why in words: it never shows zero.
 */
export function NumberRow({ cards }: Props) {
  return (
    <ul aria-label="The fabric in numbers" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {cards.map((card) => (
        <li
          key={card.id}
          data-state={card.state}
          className="flex min-w-0 flex-col gap-2 rounded-lg border border-panel-line bg-surface p-6 shadow-panel"
        >
          <div className="flex items-center justify-between gap-4">
            <h2 className="text-meta font-semibold tracking-widest text-muted uppercase">{card.label}</h2>
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-sunken text-muted">
              <svg
                aria-hidden="true"
                viewBox="0 0 24 24"
                className="h-4 w-4"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.75"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                {ICONS[card.id]}
              </svg>
            </span>
          </div>
          {card.value !== null ? (
            <>
              <p className="text-number-lg font-semibold tracking-tight break-words tabular-nums">{card.value}</p>
              <p className="text-meta text-muted">{card.context}</p>
            </>
          ) : (
            <p className="text-body">{card.context}</p>
          )}
          <a
            href={`#${card.target}`}
            aria-label={`${card.label}: view more`}
            className="mt-auto self-start text-meta text-muted underline underline-offset-2 hover:text-ink"
          >
            View more
          </a>
        </li>
      ))}
    </ul>
  );
}
