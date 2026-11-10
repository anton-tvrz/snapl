import { useCallback, useEffect, useMemo, useState } from "react";

import type { DeviceView, TopologyNode } from "./api/types";
import { Command } from "./components/Command";
import { DeviceDrawer } from "./components/DeviceDrawer";
import { LoopStrip } from "./components/LoopStrip";
import { Shell } from "./components/Shell";
import { SourceStrip } from "./components/SourceStrip";
import { Headline } from "./components/timeline/Headline";
import { Lanes } from "./components/timeline/Lanes";
import { Scrubber } from "./components/timeline/Scrubber";
import { DeviceList } from "./components/topology/DeviceList";
import { Topology } from "./components/topology/Topology";
import { ViewFrame } from "./components/ViewFrame";
import type { DataProvider, RunId } from "./data/provider";
import { usePolling } from "./data/usePolling";
import { activityFor } from "./lib/activity";
import { domainOf, scanStops } from "./lib/lanes";
import { linkNotes } from "./lib/linkNotes";
import { markedLinks } from "./lib/paths";
import { localText, utcText } from "./lib/time";
import { parseUrlState, writeUrlState } from "./lib/urlState";

// 010's refresh, and the topology on the Source of Truth's own cache time.
const OVERVIEW_MS = 5000;
const TOPOLOGY_MS = 30_000;
// The lanes, and every view of a past time, refresh once a minute: the past
// changes only when a late audit row arrives (research R11, R12).
const HISTORY_MS = 60_000;

const NO_FLAGS = new Map<string, number>();

interface Props {
  provider: DataProvider;
}

function neverScanned(node: TopologyNode): DeviceView {
  return { device_id: node.device_id, name: node.name, role: node.role, in_intent: true, drift_state: "never_scanned" };
}

/**
 * The designed dashboard: the fabric as a topology, each device's detail, and
 * every source's state. The panels for runs, schedule, CI and metrics arrive
 * in later slices; until then the plain page has them.
 */
export function App({ provider }: Props) {
  const [initial] = useState(() => parseUrlState(location.search));
  const [selected, setSelected] = useState<string | undefined>(initial.device);
  const [at, setAt] = useState<Date | undefined>(initial.at);
  const [chosenRun, setChosenRun] = useState<RunId | undefined>(initial.run);
  const range = initial.from && initial.to ? { start: initial.from, end: initial.to } : undefined;

  const mode = usePolling(provider.mode, null);
  const overview = usePolling(provider.overview, OVERVIEW_MS);
  const topology = usePolling(provider.topology, TOPOLOGY_MS);
  const sources = usePolling(provider.sources, OVERVIEW_MS);
  const timeline = usePolling(
    () => provider.timeline(range),
    HISTORY_MS,
    range ? `${range.start.toISOString()}/${range.end.toISOString()}` : "default",
  );

  // The past: asked for when the time changes and once a minute, never every
  // 5 s. A null key asks nothing.
  const atKey = at ? at.toISOString() : null;
  const pastDevices = usePolling(() => provider.devices(at), HISTORY_MS, atKey);
  const pastRuns = usePolling(() => provider.runs({ at }), HISTORY_MS, atKey);
  const lanes = timeline.data?.timeline;
  const lanesData = lanes?.data ?? undefined;
  const lanesStart = lanesData?.start;
  const pastTimeline = usePolling(
    () => provider.timeline({ start: new Date(lanesStart ?? 0), end: at ?? new Date() }),
    HISTORY_MS,
    at && lanesStart ? `${lanesStart}|${at.toISOString()}` : null,
  );

  useEffect(() => {
    writeUrlState({ ...parseUrlState(location.search), device: selected, at, run: chosenRun });
  }, [selected, at, chosenRun]);

  const onSelect = useCallback((name: string) => setSelected((current) => (current === name ? undefined : name)), []);
  const onClose = useCallback(() => setSelected(undefined), []);
  const onSelectRun = useCallback((run: RunId) => setChosenRun(run), []);

  // The page's clock is the server's: the wall clock when live, the archive's
  // creation time otherwise. Nothing is dated by the browser's clock.
  const generatedAt = overview.data?.generated_at ?? sources.data?.generated_at ?? mode.data?.now;
  const now = useMemo(() => new Date(generatedAt ?? Date.now()), [generatedAt]);
  const shown = at ?? now;
  const unreachable = mode.error ?? overview.error ?? sources.error;

  const graph = topology.data?.topology;
  const devicesView = at ? pastDevices.data?.devices : overview.data?.devices;
  const devices = useMemo(() => devicesView?.data ?? [], [devicesView]);
  const runs = useMemo(
    () => (at ? pastRuns.data?.runs.data : overview.data?.runs.data) ?? [],
    [at, pastRuns.data, overview.data],
  );
  const nodes = graph?.data?.nodes;
  const links = graph?.data?.links;

  const marked = useMemo(() => markedLinks(devices, links ?? []), [devices, links]);
  // At a past time nothing is running as far as the page knows: the running
  // workflows and the schedule's failures are today's, so they are left out.
  const activity = useMemo(
    () =>
      activityFor({
        running: at ? undefined : overview.data?.running.data,
        runs,
        now: shown.toISOString(),
        devices: new Set((nodes ?? devices).map((device) => device.name)),
      }),
    [at, overview.data, runs, shown, nodes, devices],
  );
  const flagged = useMemo(
    () =>
      at
        ? NO_FLAGS
        : new Map((overview.data?.schedule.data?.repeated_failures ?? []).map((f) => [f.name, f.consecutive_failures])),
    [at, overview.data],
  );

  // The loop strip shows the chosen run, or the latest run as of the time shown.
  // A run still going is asked again every 5 s; a finished one never changes.
  const latest = runs[0];
  const loopRun = chosenRun ?? (latest && { workflow_id: latest.workflow_id, started_at: latest.started_at });
  const loopRunning = runs.some(
    (run) => run.workflow_id === loopRun?.workflow_id && run.started_at === loopRun.started_at && run.state === "running",
  );
  const steps = usePolling(
    () => provider.steps(loopRun ?? { workflow_id: "", started_at: "" }),
    loopRunning ? OVERVIEW_MS : null,
    loopRun ? `${loopRun.workflow_id}@${loopRun.started_at}|${loopRunning}` : null,
  );
  const loop = steps.data?.steps;

  const domain = useMemo(() => (lanesData ? domainOf(lanesData) : undefined), [lanesData]);
  const stops = useMemo(() => (lanesData ? scanStops(lanesData.lanes) : []), [lanesData]);
  const told = at ? pastTimeline.data?.timeline.data : lanesData;

  // The selected device's row: from the device view, or a never-scanned row
  // for a device intent has and no scan has seen yet.
  const known = devices.find((device) => device.name === selected);
  const node = nodes?.find((n) => n.name === selected);
  const open = known ?? (node ? neverScanned(node) : undefined);
  const fabricReady = !at || devicesView !== undefined;

  return (
    <Shell useCase={mode.data?.use_case ?? overview.data?.use_case} mode={mode.data?.mode}>
      {unreachable && (
        <div role="alert" className="mb-4 rounded-lg border border-warn-line bg-warn-surface p-3 text-sm">
          <p className="font-medium">{unreachable.message}</p>
          <p className="mt-2">
            <Command text="uv run snapl dashboard" />
          </p>
        </div>
      )}

      {at && (
        <section
          aria-label="Past time"
          className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border-2 border-ink bg-sunken p-3 text-sm"
        >
          <div className="min-w-0 flex-1">
            <p className="font-semibold">
              Showing the fabric as of {utcText(at)} ({localText(at)} your time)
            </p>
            {domain?.history && at < domain.history && (
              <p className="mt-1">History starts at {utcText(domain.history)}: nothing was recorded before it.</p>
            )}
          </div>
          <button
            type="button"
            onClick={() => setAt(undefined)}
            className="rounded border border-ink bg-surface px-3 py-1 font-medium"
          >
            Return to now
          </button>
        </section>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="min-w-0">
          <ViewFrame title="Fabric" view={graph} now={now}>
            {at && (
              <p className="mb-3 text-xs text-muted">
                Devices and links are today's: the Source of Truth keeps no history. Their states are as of the
                time shown.
              </p>
            )}
            {graph?.data && fabricReady && (
              <Topology
                topology={graph.data}
                devices={devices}
                activity={activity}
                marked={marked}
                flagged={flagged}
                selected={selected}
                onSelect={onSelect}
              />
            )}
            {!fabricReady && <p className="text-sm text-muted">Loading the fabric at that time…</p>}
          </ViewFrame>
          {graph && !graph.data && devicesView && (
            <div className="mt-3">
              <DeviceList devices={devices} selected={selected} onSelect={onSelect} />
            </div>
          )}
        </div>
        {open && (
          <DeviceDrawer
            key={open.name}
            device={open}
            runs={runs}
            linkNotes={graph?.data ? linkNotes(graph.data, open.name) : []}
            now={shown}
            onClose={onClose}
            onSelectRun={onSelectRun}
            selectedRun={chosenRun}
          />
        )}
      </div>

      {loopRun && (
        <div className="mt-4">
          <ViewFrame title="The loop" view={loop} now={now} error={steps.error}>
            <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
              <span>{chosenRun ? "Chosen run" : at ? "Latest run as of the time shown" : "Latest run"}</span>
              {chosenRun && (
                <button
                  type="button"
                  onClick={() => setChosenRun(undefined)}
                  className="rounded border border-line px-2 py-0.5 text-ink"
                >
                  Show the latest run
                </button>
              )}
            </div>
            {loop?.data ? (
              <LoopStrip steps={loop.data} />
            ) : (
              <p className="text-sm text-muted">The audit trail holds no record of this run.</p>
            )}
          </ViewFrame>
        </div>
      )}

      <div className="mt-4">
        <ViewFrame title="Drift over time" view={lanes} now={now} error={timeline.error}>
          {told && <Headline timeline={told} asOf={shown} />}
          {lanesData && domain && (
            <>
              <Lanes
                timeline={lanesData}
                domain={domain}
                now={now}
                cursor={at}
                error={timeline.error}
                onSelectRun={onSelectRun}
              />
              {lanesData.lanes.length > 0 && <Scrubber domain={domain} stops={stops} value={at} onChange={setAt} />}
            </>
          )}
        </ViewFrame>
      </div>

      <section aria-label="Sources">
        <h2 className="mt-6 mb-3 text-sm font-semibold">Sources</h2>
        {at && <p className="mb-3 text-xs text-muted">Shows only the present: these are the sources' states now.</p>}
        <SourceStrip sources={sources.data?.sources} now={now} />
      </section>
      <p className="mt-6 text-sm text-muted">
        Runs, the schedule, CI, lab evidence and metrics are on the{" "}
        <a href="/plain" className="underline underline-offset-2">
          plain page
        </a>{" "}
        until their designed panels arrive.
      </p>
    </Shell>
  );
}
