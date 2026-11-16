import { Clock } from "./Clock";
import { type ReactNode, useEffect, useState } from "react";

import { lookup, type Open } from "../api/open";
import { applyTheme, readTheme, storeTheme, type Theme } from "../lib/theme";

interface Props {
  useCase: string | undefined;
  mode: Open<"live" | "archive" | "snapshot"> | undefined;
  /** The time the page is showing, from the server. */
  at?: Date;
  /** True when that time is the live present, so the clock runs. */
  running?: boolean;
  children: ReactNode;
}

const MODE_LABELS = { live: "Live", archive: "Archive", snapshot: "Recording" } as const;

/** The page frame: what is being shown, in which mode, and that it only reads. */
export function Shell({ useCase, mode, at, running = false, children }: Props) {
  const [theme, setTheme] = useState<Theme>(readTheme);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const onTheme = (next: Theme) => {
    storeTheme(next);
    setTheme(next);
  };

  return (
    <div className="mx-auto max-w-[90rem] px-4 py-4 sm:px-6">
      <header className="mb-6 flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line pb-4">
        <p className="text-title font-semibold tracking-tight">snapl</p>
        {useCase && <p className="font-mono text-body text-muted">{useCase}</p>}
        {mode && (
          <p className="chip rounded-full border border-line px-2 py-0.5 text-meta">
            {lookup<string>(MODE_LABELS, mode, (raw) => raw)}
          </p>
        )}
        <p className="chip rounded-full border border-line px-2 py-0.5 text-meta text-muted">Read-only</p>
        <Clock at={at} running={running} />
        <nav className="ml-auto flex items-center gap-4 text-body">
          {mode !== "snapshot" && (
            <a href="/plain" className="text-muted underline-offset-2 hover:text-ink hover:underline">
              Plain page
            </a>
          )}
          <label className="flex items-center gap-2 text-muted">
            <span>Theme</span>
            <select
              aria-label="Theme"
              value={theme}
              onChange={(event) => onTheme(event.target.value as Theme)}
              className="chip rounded border border-line bg-surface px-1.5 py-0.5 text-ink"
            >
              <option value="system">System</option>
              <option value="light">Light</option>
              <option value="dark">Dark</option>
            </select>
          </label>
        </nav>
      </header>
      <main>{children}</main>
    </div>
  );
}
