import { type ReactNode, useEffect, useState } from "react";

import { lookup, type Open } from "../api/open";
import { applyTheme, readTheme, storeTheme, type Theme } from "../lib/theme";

interface Props {
  useCase: string | undefined;
  mode: Open<"live" | "archive" | "snapshot"> | undefined;
  children: ReactNode;
}

const MODE_LABELS = { live: "Live", archive: "Archive", snapshot: "Recording" } as const;

/** The page frame: what is being shown, in which mode, and that it only reads. */
export function Shell({ useCase, mode, children }: Props) {
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
      <header className="mb-6 flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line pb-3">
        <p className="text-base font-semibold tracking-tight">snapl</p>
        {useCase && <p className="font-mono text-sm text-muted">{useCase}</p>}
        {mode && (
          <p className="rounded-full border border-line px-2 py-0.5 text-xs">
            {lookup<string>(MODE_LABELS, mode, (raw) => raw)}
          </p>
        )}
        <p className="rounded-full border border-line px-2 py-0.5 text-xs text-muted">Read-only</p>
        <nav className="ml-auto flex items-center gap-4 text-sm">
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
              className="rounded border border-line bg-surface px-1.5 py-0.5 text-ink"
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
