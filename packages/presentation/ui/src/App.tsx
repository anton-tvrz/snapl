import { useCallback, useEffect, useMemo, useState } from "react";

import type { DeviceView, MetricsViewName, TopologyNode } from "./api/types";
import { Command, Recording } from "./components/Command";
import { DeviceDrawer } from "./components/DeviceDrawer";
import { GroupLabel } from "./components/GroupLabel";
import { ModeBanner } from "./components/ModeBanner";
import { NumberRow } from "./components/NumberRow";
import { LoopStrip } from "./components/LoopStrip";
import { Ci } from "./components/panels/Ci";
import { History } from "./components/panels/History";
import { DriftByDevice } from "./components/panels/DriftByDevice";
import { FabricHistory } from "./components/panels/FabricHistory";
import { LabEvidence } from "./components/panels/LabEvidence";
import { Links } from "./components/panels/Links";
import { MetricView } from "./components/panels/Metrics";
import { RangePicker } from "./components/panels/RangePicker";
import { Running, Runs } from "./components/panels/Runs";
import { Schedule } from "./components/panels/Schedule";
import { Undesired } from "./components/panels/Undesired";
import { Shell } from "./components/Shell";
import { SourceStrip } from "./components/SourceStrip";
import { Headline } from "./components/timeline/Headline";
import { Lanes } from "./components/timeline/Lanes";
import { Scrubber } from "./components/timeline/Scrubber";
import { DeviceList } from "./components/topology/DeviceList";
import { FabricSummary } from "./components/topology/FabricSummary";
import { Topology } from "./components/topology/Topology";
import { ArchiveMode, PanelFrame, ViewFrame } from "./components/ViewFrame";
import type { DataProvider, RunId } from "./data/provider";
import { usePolling } from "./data/usePolling";
import { activityFor } from "./lib/activity";
import { FABRIC_SERIES } from "./lib/fabricHistory";
import { bucketFor } from "./lib/history";
import { domainOf, type Range, scanStops } from "./lib/lanes";
import { groupViews, METRIC_VIEWS } from "./lib/metrics";
import { numberCards } from "./lib/numbers";
import { fabricSummary } from "./lib/summary";
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
// The metrics in their groups (spec 015). The page asks for the views it knows.
const METRIC_GROUPS_ON_PAGE = groupViews(METRIC_VIEWS.map((view) => view.name));

interface Props {
  provider: DataProvider;
}

function neverScanned(node: TopologyNode): DeviceView {
  return {
    device_id: node.device_id,
    name: node.name,
    role: node.role,
    in_intent: true,
    drift_state: "never_scanned",
  };
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
  // One range for the lanes and the metrics. Unset follows now (each view's own default).
  const [range, setRange] = useState<Range | undefined>(() =>
    initial.from && initial.to
      ? { start: initial.from, end: initial.to }
      : undefined,
  );

  const mode = usePolling(provider.mode, null);
  // An archive does not change: in archive mode nothing is asked twice (FR-036).
  const archived = mode.data?.mode === "archive";
  const recording = mode.data?.mode === "snapshot";
  // Neither an archive nor a snapshot changes: nothing is asked twice (FR-036, FR-041).
  const fixed = archived || recording;
  const every = (ms: number) => (fixed ? null : ms);
  const overview = usePolling(provider.overview, every(OVERVIEW_MS));
  const topology = usePolling(provider.topology, every(TOPOLOGY_MS));
  const sources = usePolling(provider.sources, every(OVERVIEW_MS));
  const linkList = usePolling(provider.links, null);
  const timeline = usePolling(
    () => provider.timeline(range),
    every(HISTORY_MS),
    range
      ? `${range.start.toISOString()}/${range.end.toISOString()}`
      : "default",
  );

  const historyAnswer = usePolling(
    () =>
      provider.history(
        range,
        range
          ? bucketFor(range.end.getTime() - range.start.getTime())
          : undefined,
      ),
    every(HISTORY_MS),
    range
      ? `${range.start.toISOString()}/${range.end.toISOString()}`
      : "default",
  );

  // The number row's runs: the last 24 hours, whatever range the page shows (spec 015 FR-009).
  const runsAnswer = usePolling(() => provider.history(undefined, undefined, ["runs"]), every(HISTORY_MS));

  // The fabric history over the same range and buckets as the history (spec 015).
  const fabricHistory = usePolling(
    () => provider.history(range, range ? bucketFor(range.end.getTime() - range.start.getTime()) : undefined, FABRIC_SERIES),
    every(HISTORY_MS),
    range
      ? `${range.start.toISOString()}/${range.end.toISOString()}`
      : "default",
  );

  // Drift by device over the same range as the history (spec 015).
  const driftSummary = usePolling(
    () => provider.driftSummary(range),
    every(HISTORY_MS),
    range
      ? `${range.start.toISOString()}/${range.end.toISOString()}`
      : "default",
  );

  // The past: asked for when the time changes and once a minute, never every
  // 5 s. A null key asks nothing.
  const atKey = at ? at.toISOString() : null;
  const pastDevices = usePolling(
    () => provider.devices(at),
    every(HISTORY_MS),
    atKey,
  );
  const pastRuns = usePolling(
    () => provider.runs({ at }),
    every(HISTORY_MS),
    atKey,
  );
  const lanes = timeline.data?.timeline;
  const lanesData = lanes?.data ?? undefined;
  const lanesStart = lanesData?.start;
  const pastTimeline = usePolling(
    () =>
      provider.timeline({
        start: new Date(lanesStart ?? 0),
        end: at ?? new Date(),
      }),
    every(HISTORY_MS),
    at && lanesStart ? `${lanesStart}|${at.toISOString()}` : null,
  );

  useEffect(() => {
    writeUrlState({
      ...parseUrlState(location.search),
      device: selected,
      at,
      run: chosenRun,
      from: range?.start,
      to: range?.end,
    });
  }, [selected, at, chosenRun, range]);

  const onSelect = useCallback(
    (name: string) =>
      setSelected((current) => (current === name ? undefined : name)),
    [],
  );
  const onClose = useCallback(() => setSelected(undefined), []);
  const onSelectRun = useCallback((run: RunId) => setChosenRun(run), []);

  // The page's clock is the server's: the wall clock when live, the archive's
  // creation time otherwise. Nothing is dated by the browser's clock.
  const generatedAt =
    overview.data?.generated_at ?? sources.data?.generated_at ?? mode.data?.now;
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

  const marked = useMemo(
    () => markedLinks(devices, links ?? []),
    [devices, links],
  );
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
        : new Map(
            (overview.data?.schedule.data?.repeated_failures ?? []).map((f) => [
              f.name,
              f.consecutive_failures,
            ]),
          ),
    [at, overview.data],
  );

  const archiveInfo = mode.data?.archive;
  const exportedRange =
    archiveInfo?.metrics_start && archiveInfo.metrics_end
      ? {
          start: new Date(archiveInfo.metrics_start),
          end: new Date(archiveInfo.metrics_end),
        }
      : undefined;

  // The loop strip shows the chosen run, or the latest run as of the time shown.
  // A run still going is asked again every 5 s; a finished one never changes.
  const latest = runs[0];
  const loopRun =
    chosenRun ??
    (latest && {
      workflow_id: latest.workflow_id,
      started_at: latest.started_at,
    });
  const loopRunning = runs.some(
    (run) =>
      run.workflow_id === loopRun?.workflow_id &&
      run.started_at === loopRun.started_at &&
      run.state === "running",
  );
  const steps = usePolling(
    () => provider.steps(loopRun ?? { workflow_id: "", started_at: "" }),
    // At a past time the steps are as recorded now, so asking again adds nothing.
    !at && loopRunning ? OVERVIEW_MS : null,
    loopRun
      ? `${loopRun.workflow_id}@${loopRun.started_at}|${loopRunning}`
      : null,
  );
  const loop = steps.data?.steps;

  const domain = useMemo(
    () => (lanesData ? domainOf(lanesData) : undefined),
    [lanesData],
  );
  const stops = useMemo(
    () => (lanesData ? scanStops(lanesData.lanes) : []),
    [lanesData],
  );
  const toldView = at ? pastTimeline.data?.timeline : lanes;
  const told = toldView?.data ?? undefined;
  // The number row: derived from the answers above, as of the time shown.
  const cards = useMemo(
    () =>
      numberCards({
        devices: devicesView,
        timeline: toldView,
        runs: runsAnswer.data ?? undefined,
        now: shown,
        past: !!at,
      }),
    [devicesView, toldView, runsAnswer.data, shown, at],
  );

  // The selected device's row: from the device view, or a never-scanned row
  // for a device intent has and no scan has seen yet.
  const known = devices.find((device) => device.name === selected);
  const node = nodes?.find((n) => n.name === selected);
  const open = known ?? (node ? neverScanned(node) : undefined);
  const fabricReady = !at || devicesView !== undefined;
  const summary = useMemo(() => fabricSummary(devices), [devices]);

  return (
    <Shell
      useCase={mode.data?.use_case ?? overview.data?.use_case}
      mode={mode.data?.mode}
      at={generatedAt ? shown : undefined}
      running={mode.data?.mode === "live" && !at}
    >
      <ArchiveMode.Provider value={archived}>
        <Recording.Provider value={recording}>
          <ModeBanner mode={mode.data} />
          {unreachable && (
            <div
              role="alert"
              className="mb-4 flex flex-col gap-2 rounded-lg border border-warn-line bg-warn-surface p-4 text-body"
            >
              <p className="max-w-prose font-medium">{unreachable.message}</p>
              <p>
                <Command text="uv run snapl dashboard" />
              </p>
            </div>
          )}

          {at && (
            <section
              aria-label="Past time"
              className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border-2 border-ink bg-sunken p-4 text-body"
            >
              <div className="min-w-0 flex-1">
                <p className="font-semibold">
                  Showing the fabric as of {utcText(at)} ({localText(at)} your
                  time)
                </p>
                {domain?.history && at < domain.history && (
                  <p>
                    History starts at {utcText(domain.history)}: nothing was
                    recorded before it.
                  </p>
                )}
              </div>
              <button
                type="button"
                onClick={() => setAt(undefined)}
                className="chip rounded border border-ink bg-surface px-4 py-1 font-medium"
              >
                Return to now
              </button>
            </section>
          )}

          <div className="mb-4">
            <NumberRow cards={cards} />
          </div>

          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
            <div className="min-w-0">
              <ViewFrame title="Fabric" view={graph} now={now} id="fabric">
                {at && (
                  <p className="mb-4 text-meta text-muted">
                    Devices and links are today's: the Source of Truth keeps no
                    history. Their states are as of the time shown.
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
                {!fabricReady && (
                  <p className="text-body text-muted">
                    Loading the fabric at that time…
                  </p>
                )}
              </ViewFrame>
              {graph && !graph.data && devicesView && (
                <div className="mt-4">
                  <DeviceList
                    devices={devices}
                    selected={selected}
                    onSelect={onSelect}
                  />
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
            {/* With no device selected, the fabric's own summary takes the drawer's place. */}
            {!open && devicesView?.data && (
              <PanelFrame title="In sync" footer={at ? "As of the time shown" : undefined}>
                <FabricSummary summary={summary} onSelect={onSelect} />
              </PanelFrame>
            )}
          </div>

          {loopRun && (
            <div className="mt-4">
              <ViewFrame
                title="The loop"
                view={loop}
                now={now}
                error={steps.error}
              >
                <div className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-meta text-muted">
                  <span>
                    {chosenRun
                      ? "Chosen run"
                      : at
                        ? "Latest run as of the time shown"
                        : "Latest run"}
                  </span>
                  {chosenRun && (
                    <button
                      type="button"
                      onClick={() => setChosenRun(undefined)}
                      className="chip rounded border border-line px-2 py-0.5 text-ink"
                    >
                      Show the latest run
                    </button>
                  )}
                </div>
                {at && (
                  <p className="mb-4 text-meta text-muted">
                    These are its steps as the audit trail records them now,
                    which can include steps after the time shown.
                  </p>
                )}
                {loop?.data ? (
                  <LoopStrip steps={loop.data} />
                ) : (
                  <p className="text-body text-muted">
                    The audit trail holds no record of this run.
                  </p>
                )}
              </ViewFrame>
            </div>
          )}

          {/* One range governs the lanes, the history and the metrics, so its
              toolbar stays on screen while they scroll (spec 014 FR-027). */}
          <div data-range-scope="" className="mt-6">
            <GroupLabel>Drift and history</GroupLabel>
          {!recording && (
            <div className="sticky top-2 z-10">
              <RangePicker
                // An archive's time arrives with its first answer: the custom fields start from it.
                key={
                  range
                    ? `${range.start.getTime()}`
                    : archived
                      ? `archive-${now.getTime()}`
                      : "now"
                }
                value={range}
                now={now}
                onChange={setRange}
                archived={exportedRange}
              />
            </div>
          )}

          <div className="mt-4">
            <ViewFrame
              title="Drift over time"
              id="drift-over-time"
              view={lanes}
              now={now}
              error={timeline.error}
            >
              {told && <Headline timeline={told} asOf={shown} />}
              {lanesData && domain && (
                <>
                  <Lanes
                    timeline={lanesData}
                    domain={domain}
                    now={now}
                    selected={selected}
                    cursor={at}
                    error={timeline.error}
                    onSelectRun={onSelectRun}
                  />
                  {lanesData.lanes.length > 0 && (
                    <Scrubber
                      domain={domain}
                      stops={stops}
                      value={at}
                      onChange={setAt}
                    />
                  )}
                </>
              )}
            </ViewFrame>
          </div>

          <div className="mt-4">
            <History
              id="history"
              history={historyAnswer.data}
              now={now}
              error={historyAnswer.error}
            />
          </div>

          <div className="mt-4">
            <FabricHistory
              id="fabric-history"
              history={fabricHistory.data ?? undefined}
              // Now, not as of the time shown: the reference is labelled "in intent now".
              inIntent={overview.data?.devices.data?.filter((device) => device.in_intent).length}
              error={fabricHistory.error}
            />
          </div>

          <div className="mt-4">
            <DriftByDevice
              id="drift-by-device"
              summary={driftSummary.data?.summary}
              now={now}
              error={driftSummary.error}
              onSelect={onSelect}
            />
          </div>

          <section aria-label="Metrics" className="mt-6">
            <GroupLabel>Metrics</GroupLabel>
            <div className="flex flex-col gap-6">
              {METRIC_GROUPS_ON_PAGE.map((group) => (
                <div key={group.id} role="group" aria-label={group.label}>
                  <GroupLabel level={3}>{group.label}</GroupLabel>
                  <div className="grid gap-4 lg:grid-cols-2">
                    {group.views.map((view) => (
                      <MetricView
                        key={view.name}
                        provider={provider}
                        name={view.name as MetricsViewName}
                        title={view.title}
                        kind={view.kind}
                        range={fixed ? undefined : range}
                        live={!fixed}
                        now={now}
                      />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </section>
          </div>

          <section aria-label="Operations" className="mt-6">
            <GroupLabel>Operations</GroupLabel>
          <div className="grid gap-4 lg:grid-cols-2">
            <Schedule
              schedule={overview.data?.schedule}
              worker={overview.data?.worker}
              now={now}
              presentOnly={!!at}
            />
            <Running
              running={overview.data?.running}
              now={now}
              presentOnly={!!at}
            />
          </div>
          <div className="mt-4">
            <Runs
              key={selected ?? ""}
              runs={at ? pastRuns.data?.runs : overview.data?.runs}
              now={shown}
              device={selected}
            />
          </div>
          <div className="mt-4">
            <Undesired devices={devicesView} now={now} />
          </div>
          </section>

          <section aria-label="Evidence" className="mt-6">
            <GroupLabel>Evidence</GroupLabel>
          <div className="grid gap-4 lg:grid-cols-2">
            <Ci ci={overview.data?.ci} now={now} presentOnly={!!at} />
            <LabEvidence
              lab={overview.data?.lab}
              now={now}
              presentOnly={!!at}
            />
          </div>
          </section>

          <section aria-label="Sources" className="mt-6">
            <GroupLabel>Sources</GroupLabel>
            {at && (
              <p className="mb-4 text-meta text-muted">
                Shows only the present: these are the sources' states now.
              </p>
            )}
            <SourceStrip sources={sources.data?.sources} now={now} />
          </section>
          <div className="mt-6">
            <Links links={linkList.data} />
          </div>
        </Recording.Provider>
      </ArchiveMode.Provider>
    </Shell>
  );
}
