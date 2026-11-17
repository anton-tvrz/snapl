import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { ApiError } from "./data/http";
import type { DataProvider } from "./data/provider";
import { DRIFT_SUMMARY, FABRIC_HISTORY, HISTORY, LINKS, METRICS, MODE, NOW, OVERVIEW, OVERVIEW_UP, SOURCES, STEPS_DEPLOY, TIMELINE, TOPOLOGY } from "./test/fixtures";

// Every folded panel and group open (#290).
const ALL_OPEN = "/?open=history,fabric-history,drift-by-device,metrics-timing,metrics-work,metrics-worker,metrics-dashboard";

function provider(overrides: Partial<DataProvider> = {}): DataProvider {
  const unused = async () => {
    throw new Error("not asked in this slice");
  };
  return {
    mode: async () => MODE,
    sources: async () => SOURCES,
    overview: async () => OVERVIEW,
    topology: async () => TOPOLOGY,
    links: async () => LINKS,
    devices: unused,
    runs: unused,
    timeline: async () => TIMELINE,
    history: async (_range?: unknown, _bucket?: unknown, series?: unknown) => (series ? FABRIC_HISTORY : HISTORY),
    driftSummary: async () => DRIFT_SUMMARY,
    steps: async () => STEPS_DEPLOY,
    metrics: async (view: keyof typeof METRICS) => METRICS[view],
    ...overrides,
  } as DataProvider;
}

const device = (name: string) => screen.getByRole("button", { name: new RegExp(`^${name},`) });

beforeEach(() => {
  history.replaceState(null, "", "/");
});

afterEach(() => {
  vi.useRealTimers();
});

describe("App", () => {
  it("draws the fabric from the topology and the overview's device states", async () => {
    render(<App provider={provider()} />);

    expect(await screen.findByRole("group", { name: "Fabric topology" })).toBeInTheDocument();
    expect(device("leaf-01")).toHaveAccessibleName("leaf-01, leaf, Undesired config");
    expect(device("spine-01")).toHaveAccessibleName("spine-01, spine, In sync");
    expect(screen.getByRole("heading", { name: "Fabric" })).toBeInTheDocument();
  });

  it("leads with the fabric in four numbers, above the fabric, each linked to its panel", async () => {
    render(<App provider={provider()} />);

    const row = await screen.findByRole("list", { name: "The fabric in numbers" });
    expect(await within(row).findByText("5 of 6")).toBeInTheDocument();
    expect(within(row).getByText("5 s")).toBeInTheDocument();
    expect(await within(row).findByText("None failed.")).toBeInTheDocument();
    const fabric = screen.getByRole("region", { name: "Fabric" });
    expect(row.compareDocumentPosition(fabric) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    for (const link of within(row).getAllByRole("link")) {
      expect(document.querySelector(link.getAttribute("href")!)).not.toBeNull();
    }
  });

  // spec 014 US5: the fabric's own summary fills the space beside it.
  it("shows the share in sync and the drifted devices beside the fabric until a device is selected", async () => {
    render(<App provider={provider()} />);

    const summary = await screen.findByRole("region", { name: "In sync" });
    expect(within(summary).getByRole("img", { name: "5 of 6 devices in sync" })).toBeInTheDocument();
    await userEvent.click(within(summary).getByRole("button", { name: /^Open leaf-01: Undesired config/ }));

    expect(screen.getByRole("dialog", { name: "leaf-01" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "In sync" })).not.toBeInTheDocument();

    await userEvent.click(within(screen.getByRole("dialog", { name: "leaf-01" })).getByRole("button", { name: /close/i }));
    expect(screen.getByRole("region", { name: "In sync" })).toBeInTheDocument();
  });

  it("shows the server's time as a running clock in the header", async () => {
    render(<App provider={provider()} />);

    const clock = await within(screen.getAllByRole("banner")[0]!).findByRole("time");
    expect(clock).toHaveTextContent(`${NOW.toISOString().slice(11, 19)} UTC`);
    expect(clock).toHaveAttribute("data-running", "true");
  });

  it("puts the page in its order, under group labels (spec 014 FR-022)", async () => {
    render(<App provider={provider({ overview: async () => OVERVIEW_UP })} />);
    await screen.findByRole("group", { name: "Fabric topology" });
    await screen.findByRole("region", { name: "Runs" });

    const wanted = [
      "Devices in sync",
      "Fabric",
      "The loop",
      "Drift and history",
      "Drift over time",
      "Drift and deploys by hour",
      "Fabric history",
      "Drift by device",
      "Metrics",
      "Workflow duration (95th percentile)",
      "Operations",
      "Schedule and worker",
      "Running now",
      "Runs",
      "Undesired config",
      "Evidence",
      "CI",
      "Live lab evidence",
      "Sources",
      "Links",
    ];
    const headings = screen.getAllByRole("heading").map((heading) => heading.textContent);
    expect(headings.filter((text) => wanted.includes(text ?? ""))).toEqual(wanted);
  });

  it("opens a device's detail when it is selected, and puts it in the address", async () => {
    render(<App provider={provider()} />);

    await userEvent.click(await screen.findByRole("button", { name: /^leaf-01,/ }));

    const drawer = screen.getByRole("dialog", { name: "leaf-01" });
    expect(within(drawer).getByText("/interface[name=ethernet-1/7]/description")).toBeInTheDocument();
    expect(location.search).toBe("?device=leaf-01");
  });

  it("opens the device named in the address on load", async () => {
    history.replaceState(null, "", "/?device=spine-01");

    render(<App provider={provider()} />);

    expect(await screen.findByRole("dialog", { name: "spine-01" })).toBeInTheDocument();
  });

  it("ignores a device in the address that the fabric does not have", async () => {
    history.replaceState(null, "", "/?device=ghost-99");

    render(<App provider={provider()} />);

    await screen.findByRole("group", { name: "Fabric topology" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("closes the detail with Escape and clears the address", async () => {
    render(<App provider={provider()} />);
    await userEvent.click(await screen.findByRole("button", { name: /^leaf-01,/ }));

    await userEvent.keyboard("{Escape}");

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(location.search).toBe("");
  });

  it("selecting the open device again closes it", async () => {
    render(<App provider={provider()} />);
    await userEvent.click(await screen.findByRole("button", { name: /^leaf-01,/ }));

    await userEvent.click(device("leaf-01"));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("shows the device list, with the reason and the remedy, when there is no topology", async () => {
    const down = {
      ...TOPOLOGY,
      topology: { source: "sot", available: false, reason: "Infrahub is unreachable", remedy: "uv run invoke dev.deps", data: null },
    };

    render(<App provider={provider({ topology: async () => down as typeof TOPOLOGY })} />);

    expect(await screen.findByText("Infrahub is unreachable")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Fabric topology" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /^(spine|leaf)-0\d,/ })).toHaveLength(6);
  });

  it("marks a device whose scheduled repair keeps failing", async () => {
    const leaf04 = OVERVIEW.devices.data!.find((d) => d.name === "leaf-04")!;
    const flagged = {
      ...OVERVIEW,
      schedule: {
        ...OVERVIEW.schedule,
        data: {
          exists: false,
          repeated_failures: [
            {
              device_id: leaf04.device_id,
              name: "leaf-04",
              consecutive_failures: 3,
              last_failed_at: "2026-11-07T21:00:00Z",
              suggested_command: "snapl schedule mode report --use-case dcfabric",
            },
          ],
        },
      },
    };

    render(<App provider={provider({ overview: async () => flagged as typeof OVERVIEW })} />);

    expect(await screen.findByRole("button", { name: /^leaf-04,.*repair failed 3 times in a row$/ })).toBeInTheDocument();
  });

  it("still shows each source's state", async () => {
    render(<App provider={provider()} />);

    // The sources panel names it; the history panels name it in their footers too.
    expect((await screen.findAllByText("Audit trail")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("uv run invoke dev.deps").length).toBeGreaterThan(0);
  });

  it("says the server did not answer, and how to start it", async () => {
    const down = async () => {
      throw new ApiError(0, "the dashboard server did not answer");
    };
    render(<App provider={provider({ mode: down, sources: down, overview: down, topology: down })} />);

    expect(await screen.findByText("the dashboard server did not answer")).toBeInTheDocument();
    expect(screen.getByText("uv run snapl dashboard")).toBeInTheDocument();
  });

  it("reads the mode once, the overview every 5 s and the topology every 30 s", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const calls = { mode: vi.fn(), overview: vi.fn(), topology: vi.fn() };
    render(
      <App
        provider={provider({
          mode: async () => (calls.mode(), MODE),
          overview: async () => (calls.overview(), OVERVIEW),
          topology: async () => (calls.topology(), TOPOLOGY),
        })}
      />,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });

    expect(calls.mode).toHaveBeenCalledTimes(1);
    expect(calls.overview).toHaveBeenCalledTimes(7);
    expect(calls.topology).toHaveBeenCalledTimes(2);
  });

  it("shows the loop for the latest run", async () => {
    const steps = vi.fn(async () => STEPS_DEPLOY);
    render(<App provider={provider({ steps })} />);

    const loop = await screen.findByRole("region", { name: "The loop" });
    expect(steps).toHaveBeenCalledWith({
      workflow_id: "scan-drift-dcfabric-68101896-6a9c-4c65-8a84-24af2b98b12a",
      started_at: "2026-11-07T21:20:17.860408Z",
    });
    expect(await within(loop).findByRole("list", { name: "The loop, block by block" })).toBeInTheDocument();
    expect(loop).toHaveTextContent("Latest run");
  });

  it("shows the loop of a run chosen in the drawer, keeps it in the address, and goes back to the latest", async () => {
    const steps = vi.fn(async () => STEPS_DEPLOY);
    render(<App provider={provider({ steps })} />);
    await userEvent.click(await screen.findByRole("button", { name: /^spine-01,/ }));

    await userEvent.click(screen.getByRole("button", { name: "Show the loop for DeployIntent started 2026-11-07 21:20:15 UTC" }));

    expect(steps).toHaveBeenLastCalledWith({
      workflow_id: "deploy-intent-18e29d6d-e0c5-0ba7-32d1-c510fc3ef077",
      started_at: "2026-11-07T21:20:15.648265Z",
    });
    expect(new URLSearchParams(location.search).get("run")).toBe(
      "deploy-intent-18e29d6d-e0c5-0ba7-32d1-c510fc3ef077@2026-11-07T21:20:15.648265Z",
    );
    const loop = screen.getByRole("region", { name: "The loop" });
    expect(loop).toHaveTextContent("Chosen run");

    await userEvent.click(within(loop).getByRole("button", { name: "Show the latest run" }));

    expect(new URLSearchParams(location.search).has("run")).toBe(false);
    expect(loop).toHaveTextContent("Latest run");
  });

  it("opens the run named in the address", async () => {
    history.replaceState(null, "", "/?run=reconcile-217b6000-0e6c-4919-81d9-9f6a1075cd1d@2026-11-07T21:20:15.472996Z");
    const steps = vi.fn(async () => STEPS_DEPLOY);

    render(<App provider={provider({ steps })} />);
    await screen.findByRole("region", { name: "The loop" });

    expect(steps).toHaveBeenCalledWith({
      workflow_id: "reconcile-217b6000-0e6c-4919-81d9-9f6a1075cd1d",
      started_at: "2026-11-07T21:20:15.472996Z",
    });
  });

  it("says when the audit trail holds no such run", async () => {
    const none = { ...STEPS_DEPLOY, steps: { ...STEPS_DEPLOY.steps, data: null } };
    render(<App provider={provider({ steps: async () => none })} />);

    expect(await screen.findByText("The audit trail holds no record of this run.")).toBeInTheDocument();
  });

  it("says why the loop could not be read", async () => {
    const steps = async () => {
      throw new ApiError(503, "the audit trail could not be read");
    };
    render(<App provider={provider({ steps })} />);

    const loop = await screen.findByRole("region", { name: "The loop" });
    expect(await within(loop).findByText("the audit trail could not be read")).toBeInTheDocument();
  });

  it("has a panel for the schedule and worker, what is running, the runs and undesired config", async () => {
    render(<App provider={provider({ overview: async () => OVERVIEW_UP })} />);

    expect(await screen.findByRole("region", { name: "Schedule and worker" })).toHaveTextContent("Remediate every 5 min");
    expect(screen.getByRole("region", { name: "Running now" })).toHaveTextContent("ScanDrift");
    expect(within(screen.getByRole("region", { name: "Runs" })).getAllByRole("row")).toHaveLength(13);
    expect(screen.getByRole("region", { name: "Undesired config" })).toHaveTextContent("leaf-01");
  });

  it("shows only the selected device's runs", async () => {
    render(<App provider={provider()} />);
    await userEvent.click(await screen.findByRole("button", { name: /^spine-01,/ }));

    expect(screen.getByRole("region", { name: "Runs" })).toHaveTextContent("Only spine-01's runs.");
  });

  it("has a panel for CI and the live lab evidence, and the links", async () => {
    render(<App provider={provider({ overview: async () => OVERVIEW_UP })} />);

    expect(await screen.findByRole("region", { name: "CI" })).toHaveTextContent("#212");
    expect(screen.getByRole("region", { name: "Live lab evidence" })).toHaveTextContent("3 commits on main since this run");
    expect(await screen.findByRole("region", { name: "Links" })).toHaveTextContent("Project board");
  });

  it("asks for the links once", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const links = vi.fn(async () => LINKS);
    render(<App provider={provider({ links })} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });

    expect(links).toHaveBeenCalledTimes(1);
  });

  it("has a panel for each metrics view", async () => {
    history.replaceState(null, "", ALL_OPEN);
    render(<App provider={provider()} />);

    for (const title of ["Workflow duration (95th percentile)", "Worker up", "Dashboard sources up"]) {
      expect(await screen.findByRole("region", { name: title })).toBeInTheDocument();
    }
    expect(await screen.findByRole("table", { name: "Worker up: latest values" })).toHaveTextContent("up");
  });

  // spec 015 T028: the metrics in four groups, each under its label.
  // #290 (spec 014 SC-003): only the first group is open, and a folded group asks nothing.
  it("groups the metrics under four labels, with only the loop's timing open", async () => {
    const metrics = vi.fn(async (view: keyof typeof METRICS) => METRICS[view]);
    render(<App provider={provider({ metrics })} />);

    const section = await screen.findByRole("region", { name: "Metrics" });
    const labels = ["The loop's timing", "What the loop did", "The worker", "The dashboard"];
    const groups = labels.map((name) => within(section).getByRole("group", { name }));
    for (const [before, after] of groups.slice(1).map((group, i) => [groups[i]!, group] as const)) {
      expect(before.compareDocumentPosition(after) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
    expect(within(section).queryByRole("group", { name: "Other" })).not.toBeInTheDocument();
    const titles = (group: HTMLElement) => within(group).queryAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(await within(groups[0]!).findAllByRole("region")).toHaveLength(5);
    for (const group of groups.slice(1)) expect(titles(group)).toEqual([]);
    expect(within(groups[1]!).getByRole("button", { name: "Show What the loop did" })).toHaveTextContent("Show 2 panels");
    expect(new Set(metrics.mock.calls.map(([view]) => view))).toEqual(
      new Set(["workflow-duration", "workflow-mean", "gnmi-duration", "gnmi-mean", "activity-time"]),
    );

    await userEvent.click(within(groups[1]!).getByRole("button", { name: "Show What the loop did" }));

    expect(titles(within(section).getByRole("group", { name: "What the loop did" }))).toEqual([
      "Workflows completed (count)",
      "gNMI errors (rate)",
    ]);
    expect(new URLSearchParams(location.search).get("open")).toBe("metrics-timing,metrics-work");
  });

  // #290: the history's detail panels are folded behind their headings.
  it("folds the history's detail panels, opens one with a click and keeps it in the address", async () => {
    render(<App provider={provider()} />);

    for (const name of ["Drift and deploys by hour", "Fabric history", "Drift by device"]) {
      expect(await screen.findByRole("button", { name: `Show ${name}` })).toHaveAttribute("aria-expanded", "false");
    }
    expect(screen.queryByRole("img", { name: /^Devices in sync at each scan/ })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Show Fabric history" }));

    const panel = screen.getByRole("region", { name: "Fabric history" });
    expect(await within(panel).findByRole("img", { name: /^Devices in sync at each scan/ })).toBeInTheDocument();
    expect(new URLSearchParams(location.search).get("open")).toBe("fabric-history,metrics-timing");

    await userEvent.click(screen.getByRole("button", { name: "Hide Fabric history" }));

    expect(screen.getByRole("button", { name: "Show Fabric history" })).toBeInTheDocument();
    expect(new URLSearchParams(location.search).has("open")).toBe(false);
  });

  it("opens what the address names, and closes what it leaves out", async () => {
    history.replaceState(null, "", "/?open=drift-by-device%2Cmetrics-worker");
    render(<App provider={provider()} />);

    const panel = await screen.findByRole("region", { name: "Drift by device" });
    expect(await within(panel).findByRole("button", { name: /^Open leaf-01:/ })).toBeInTheDocument();
    expect(await screen.findByRole("region", { name: "Worker up" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show The loop's timing" })).toBeInTheDocument();
  });

  it("opens the panel a number-row link points to", async () => {
    render(<App provider={provider()} />);
    await screen.findByRole("button", { name: "Show Fabric history" });

    await act(async () => {
      location.hash = "#fabric-history";
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });

    expect(screen.getByRole("button", { name: "Hide Fabric history" })).toBeInTheDocument();
    history.replaceState(null, "", "/");
  });

  it("leads the workflows-completed panel with a count and its window", async () => {
    history.replaceState(null, "", ALL_OPEN);
    render(<App provider={provider()} />);

    const panel = await screen.findByRole("region", { name: "Workflows completed (count)" });
    expect(await within(panel).findByText("1", { selector: "[data-lead]" })).toBeInTheDocument();
    expect(panel).toHaveTextContent("in the last 60 s");
  });

  it("asks the lanes and the metrics for a chosen range, and keeps it in the address", async () => {
    history.replaceState(null, "", ALL_OPEN);
    const timeline = vi.fn(async () => TIMELINE);
    const metrics = vi.fn(async (view: keyof typeof METRICS) => METRICS[view]);
    render(<App provider={provider({ timeline, metrics })} />);
    await screen.findByRole("region", { name: "Worker up" });

    await userEvent.click(screen.getByRole("button", { name: "24 h" }));

    const range = { start: new Date(NOW.getTime() - 86_400_000), end: NOW };
    expect(timeline).toHaveBeenLastCalledWith(range);
    // The page's last metrics panel, every group open.
    expect(metrics).toHaveBeenLastCalledWith("source-fetch-time", range);
    const params = new URLSearchParams(location.search);
    expect(params.get("from")).toBe(range.start.toISOString());
    expect(params.get("to")).toBe(NOW.toISOString());

    await userEvent.click(screen.getByRole("button", { name: "Follow now" }));

    expect(timeline).toHaveBeenLastCalledWith(undefined);
    expect(new URLSearchParams(location.search).has("from")).toBe(false);
  });

  it("reads the metrics every 30 s", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const metrics = vi.fn(async (view: keyof typeof METRICS) => METRICS[view]);
    render(<App provider={provider({ metrics })} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });

    // The five views of the one open group, twice (#290).
    expect(metrics).toHaveBeenCalledTimes(10);
  });

  it("has the history table, asked over the chosen range in buckets that keep it readable", async () => {
    const history = vi.fn(async () => HISTORY);
    render(<App provider={provider({ history })} />);

    expect(await screen.findByRole("region", { name: "Drift and deploys by hour" })).toBeInTheDocument();
    expect(history).toHaveBeenCalledWith(undefined, undefined);

    await userEvent.click(screen.getByRole("button", { name: "7 d" }));

    expect(history).toHaveBeenCalledWith({ start: new Date(NOW.getTime() - 7 * 86_400_000), end: NOW }, "1d");
  });

  it("has the fabric history, its four series named and asked over the chosen range", async () => {
    window.history.replaceState(null, "", ALL_OPEN);
    const history = vi.fn(async (_range?: unknown, _bucket?: unknown, series?: unknown) => (series ? FABRIC_HISTORY : HISTORY));
    render(<App provider={provider({ history })} />);

    const panel = await screen.findByRole("region", { name: "Fabric history" });
    expect(await within(panel).findByRole("img", { name: /^Devices in sync at each scan, as a chart: 3 scans/ })).toBeInTheDocument();
    const named = ["in_sync_devices", "open_drifts", "runs", "repair_duration"];
    expect(history).toHaveBeenCalledWith(undefined, undefined, named);

    await userEvent.click(screen.getByRole("button", { name: "7 d" }));

    expect(history).toHaveBeenCalledWith({ start: new Date(NOW.getTime() - 7 * 86_400_000), end: NOW }, "1d", named);
  });

  it("counts the runs of the last 24 hours in the fourth card, whatever range is chosen", async () => {
    const history = vi.fn(async (_range?: unknown, _bucket?: unknown, series?: unknown) => (series ? FABRIC_HISTORY : HISTORY));
    render(<App provider={provider({ history })} />);

    const row = await screen.findByRole("list", { name: "The fabric in numbers" });
    const card = within(row).getByRole("heading", { name: "Runs, 24 h" }).closest("li")!;
    expect(await within(card).findByText("12")).toBeInTheDocument();
    expect(card).toHaveTextContent("None failed.");
    expect(within(card).getByRole("link", { name: "Runs, 24 h: view more" })).toHaveAttribute("href", "#fabric-history");

    await userEvent.click(screen.getByRole("button", { name: "7 d" }));

    // The page's history follows the range; the card's runs never do.
    expect(history).toHaveBeenCalledWith(expect.objectContaining({ end: NOW }), "1d");
    const forCard = history.mock.calls.filter((call) => JSON.stringify(call[2]) === '["runs"]');
    expect(forCard.length).toBeGreaterThan(0);
    for (const call of forCard) expect(call.slice(0, 2)).toEqual([undefined, undefined]);
  });

  it("draws the devices in intent now over the devices in sync", async () => {
    history.replaceState(null, "", ALL_OPEN);
    render(<App provider={provider()} />);

    const panel = await screen.findByRole("region", { name: "Fabric history" });
    expect(await within(panel).findByRole("img", { name: /, against \d+ in intent now\./ })).toBeInTheDocument();
  });

  it("has drift by device, asked over the chosen range, and opens a device from it", async () => {
    history.replaceState(null, "", ALL_OPEN);
    const driftSummary = vi.fn(async () => DRIFT_SUMMARY);
    render(<App provider={provider({ driftSummary })} />);

    const panel = await screen.findByRole("region", { name: "Drift by device" });
    expect(driftSummary).toHaveBeenLastCalledWith(undefined);

    await userEvent.click(within(panel).getByRole("button", { name: /^Open leaf-01:/ }));

    expect(screen.getByRole("dialog", { name: "leaf-01" })).toBeInTheDocument();
    expect(new URLSearchParams(location.search).get("device")).toBe("leaf-01");

    await userEvent.click(screen.getByRole("button", { name: "7 d" }));

    expect(driftSummary).toHaveBeenLastCalledWith({ start: new Date(NOW.getTime() - 7 * 86_400_000), end: NOW });
  });
});
