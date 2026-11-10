import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { ApiError } from "./data/http";
import type { DataProvider } from "./data/provider";
import { MODE, OVERVIEW, SOURCES, TIMELINE, TOPOLOGY } from "./test/fixtures";

function provider(overrides: Partial<DataProvider> = {}): DataProvider {
  const unused = async () => {
    throw new Error("not asked in this slice");
  };
  return {
    mode: async () => MODE,
    sources: async () => SOURCES,
    overview: async () => OVERVIEW,
    topology: async () => TOPOLOGY,
    links: unused,
    devices: unused,
    runs: unused,
    timeline: async () => TIMELINE,
    steps: unused,
    metrics: unused,
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
});
