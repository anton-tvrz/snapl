import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { ApiError } from "./data/http";
import type { DataProvider } from "./data/provider";
import { HISTORY, LINKS, METRICS, MODE, NOW, OVERVIEW, OVERVIEW_UP, SOURCES, STEPS_DEPLOY, TIMELINE, TOPOLOGY } from "./test/fixtures";

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
    history: async () => HISTORY,
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

    expect(await screen.findByText("Audit trail")).toBeInTheDocument();
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
    render(<App provider={provider()} />);

    for (const title of ["Workflow duration (95th percentile)", "Worker up", "Dashboard sources up"]) {
      expect(await screen.findByRole("region", { name: title })).toBeInTheDocument();
    }
    expect(await screen.findByRole("table", { name: "Worker up: latest values" })).toHaveTextContent("up");
  });

  it("asks the lanes and the metrics for a chosen range, and keeps it in the address", async () => {
    const timeline = vi.fn(async () => TIMELINE);
    const metrics = vi.fn(async (view: keyof typeof METRICS) => METRICS[view]);
    render(<App provider={provider({ timeline, metrics })} />);
    await screen.findByRole("region", { name: "Worker up" });

    await userEvent.click(screen.getByRole("button", { name: "24 h" }));

    const range = { start: new Date(NOW.getTime() - 86_400_000), end: NOW };
    expect(timeline).toHaveBeenLastCalledWith(range);
    expect(metrics).toHaveBeenLastCalledWith("source-health", range);
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

    expect(metrics).toHaveBeenCalledTimes(12);
  });

  it("has the history table, asked over the chosen range in buckets that keep it readable", async () => {
    const history = vi.fn(async () => HISTORY);
    render(<App provider={provider({ history })} />);

    expect(await screen.findByRole("region", { name: "Drift and deploys by hour" })).toBeInTheDocument();
    expect(history).toHaveBeenLastCalledWith(undefined, undefined);

    await userEvent.click(screen.getByRole("button", { name: "7 d" }));

    expect(history).toHaveBeenLastCalledWith({ start: new Date(NOW.getTime() - 7 * 86_400_000), end: NOW }, "1d");
  });
});
