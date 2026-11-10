import { useCallback, useEffect, useMemo, useState } from "react";

import type { DeviceView, TopologyNode } from "./api/types";
import { Command } from "./components/Command";
import { DeviceDrawer } from "./components/DeviceDrawer";
import { Shell } from "./components/Shell";
import { SourceStrip } from "./components/SourceStrip";
import { DeviceList } from "./components/topology/DeviceList";
import { Topology } from "./components/topology/Topology";
import { ViewFrame } from "./components/ViewFrame";
import type { DataProvider } from "./data/provider";
import { usePolling } from "./data/usePolling";
import { activityFor } from "./lib/activity";
import { linkNotes } from "./lib/linkNotes";
import { markedLinks } from "./lib/paths";
import { parseUrlState, writeUrlState } from "./lib/urlState";

// 010's refresh, and the topology on the Source of Truth's own cache time.
const OVERVIEW_MS = 5000;
const TOPOLOGY_MS = 30_000;

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
  const mode = usePolling(provider.mode, null);
  const overview = usePolling(provider.overview, OVERVIEW_MS);
  const topology = usePolling(provider.topology, TOPOLOGY_MS);
  const sources = usePolling(provider.sources, OVERVIEW_MS);
  const [selected, setSelected] = useState<string | undefined>(() => parseUrlState(location.search).device);

  useEffect(() => {
    writeUrlState({ ...parseUrlState(location.search), device: selected });
  }, [selected]);

  const onSelect = useCallback((name: string) => setSelected((current) => (current === name ? undefined : name)), []);
  const onClose = useCallback(() => setSelected(undefined), []);

  // The page's clock is the server's: the wall clock when live, the archive's
  // creation time otherwise. Nothing is dated by the browser's clock.
  const generatedAt = overview.data?.generated_at ?? sources.data?.generated_at ?? mode.data?.now;
  const now = new Date(generatedAt ?? Date.now());
  const unreachable = mode.error ?? overview.error ?? sources.error;

  const graph = topology.data?.topology;
  const devicesView = overview.data?.devices;
  const devices = useMemo(() => devicesView?.data ?? [], [devicesView]);
  const nodes = graph?.data?.nodes;
  const links = graph?.data?.links;

  const marked = useMemo(() => markedLinks(devices, links ?? []), [devices, links]);
  const activity = useMemo(
    () =>
      activityFor({
        running: overview.data?.running.data,
        runs: overview.data?.runs.data,
        now: generatedAt ?? new Date().toISOString(),
        devices: new Set((nodes ?? devices).map((device) => device.name)),
      }),
    [overview.data, generatedAt, nodes, devices],
  );
  const flagged = useMemo(
    () => new Map((overview.data?.schedule.data?.repeated_failures ?? []).map((f) => [f.name, f.consecutive_failures])),
    [overview.data],
  );

  // The selected device's row: from the device view, or a never-scanned row
  // for a device intent has and no scan has seen yet.
  const known = devices.find((device) => device.name === selected);
  const node = nodes?.find((n) => n.name === selected);
  const open = known ?? (node ? neverScanned(node) : undefined);

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

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="min-w-0">
          <ViewFrame title="Fabric" view={graph} now={now}>
            {graph?.data && <Topology
              topology={graph.data}
              devices={devices}
              activity={activity}
              marked={marked}
              flagged={flagged}
              selected={selected}
              onSelect={onSelect}
            />}
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
            runs={overview.data?.runs.data ?? []}
            linkNotes={graph?.data ? linkNotes(graph.data, open.name) : []}
            now={now}
            onClose={onClose}
          />
        )}
      </div>

      <h2 className="mt-6 mb-3 text-sm font-semibold">Sources</h2>
      <SourceStrip sources={sources.data?.sources} now={now} />
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
