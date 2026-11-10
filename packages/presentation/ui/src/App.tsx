import { Command } from "./components/Command";
import { Shell } from "./components/Shell";
import { SourceStrip } from "./components/SourceStrip";
import type { DataProvider } from "./data/provider";
import { usePolling } from "./data/usePolling";

const SOURCES_MS = 5000;

interface Props {
  provider: DataProvider;
}

/**
 * The designed dashboard. This first slice is the frame: which use case, which
 * mode, and whether each source answered. The topology and the panels arrive
 * in the slices that follow, and until then the plain page has every view.
 */
export function App({ provider }: Props) {
  const mode = usePolling(provider.mode, null);
  const sources = usePolling(provider.sources, SOURCES_MS);
  // The page's clock is the server's: the wall clock when live, the archive's
  // creation time otherwise. Before the first answer there is nothing to date.
  const now = new Date(sources.data?.generated_at ?? mode.data?.now ?? Date.now());
  const unreachable = mode.error ?? sources.error;

  return (
    <Shell useCase={mode.data?.use_case ?? sources.data?.use_case} mode={mode.data?.mode}>
      {unreachable && (
        <div role="alert" className="mb-4 rounded-lg border border-warn-line bg-warn-surface p-3 text-sm">
          <p className="font-medium">{unreachable.message}</p>
          <p className="mt-2">
            <Command text="uv run snapl dashboard" />
          </p>
        </div>
      )}
      <h1 className="mb-3 text-sm font-semibold">Sources</h1>
      <SourceStrip sources={sources.data?.sources} now={now} />
      <p className="mt-6 text-sm text-muted">
        The designed views are being built slice by slice. Every view is on the{" "}
        <a href="/plain" className="underline underline-offset-2">
          plain page
        </a>{" "}
        meanwhile.
      </p>
    </Shell>
  );
}
