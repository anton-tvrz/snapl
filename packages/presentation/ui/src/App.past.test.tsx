import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import type { DataProvider, Range, RunsQuery } from "./data/provider";
import {
  DEVICES_AT_SCAN_2,
  HISTORY,
  LINKS,
  METRICS,
  MODE,
  OVERVIEW,
  RUNS_AT_SCAN_2,
  SCAN_STOPS,
  SOURCES,
  STEPS_DEPLOY,
  TIMELINE,
  TIMELINE_AT_SCAN_2,
  TOPOLOGY,
} from "./test/fixtures";

// The "as of" time (spec 011 US2): the fabric, the device detail and the runs
// at a past time, and what cannot be shown for one.

const STOP_2 = SCAN_STOPS[1]!;
const STOP_3 = SCAN_STOPS[2]!;

function provider() {
  const calls = {
    devices: vi.fn<(at?: Date) => void>(),
    runs: vi.fn<(query: RunsQuery) => void>(),
    timeline: vi.fn<(range?: Range) => void>(),
  };
  const p: DataProvider = {
    mode: async () => MODE,
    sources: async () => SOURCES,
    overview: async () => OVERVIEW,
    topology: async () => TOPOLOGY,
    devices: async (at) => (calls.devices(at), DEVICES_AT_SCAN_2),
    runs: async (query) => (calls.runs(query), RUNS_AT_SCAN_2),
    timeline: async (range) => (calls.timeline(range), range ? TIMELINE_AT_SCAN_2 : TIMELINE),
    links: async () => LINKS,
    history: async () => HISTORY,
    steps: async () => STEPS_DEPLOY,
    metrics: async (view) => METRICS[view],
  };
  return { p, calls };
}

const device = (name: string) => screen.getByRole("button", { name: new RegExp(`^${name},`) });
const banner = () => screen.getByRole("region", { name: "Past time" });

async function settle() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  history.replaceState(null, "", "/");
});

afterEach(() => {
  vi.useRealTimers();
});

describe("App at a past time", () => {
  it("asks for the devices and runs at the time in the address, and draws that fabric", async () => {
    history.replaceState(null, "", `/?at=${STOP_2}`);
    const { p, calls } = provider();

    render(<App provider={p} />);
    await settle();

    expect(calls.devices).toHaveBeenCalledWith(new Date(STOP_2));
    expect(calls.runs).toHaveBeenCalledWith({ at: new Date(STOP_2) });
    expect(await screen.findByRole("button", { name: /^spine-01,/ })).toHaveAccessibleName(
      "spine-01, spine, Missing intent",
    );
    expect(banner()).toHaveTextContent("Showing the fabric as of 2026-11-07 21:20:15 UTC");
    expect(within(banner()).getByRole("button", { name: "Return to now" })).toBeInTheDocument();
  });

  it("says which views show only the present, and that the drawing is today's", async () => {
    history.replaceState(null, "", `/?at=${STOP_2}`);

    render(<App provider={provider().p} />);
    await settle();

    expect(await screen.findByText(/devices and links are today's/i)).toBeInTheDocument();
    const sources = screen.getByRole("region", { name: "Sources" });
    expect(sources).toHaveTextContent("Shows only the present");
  });

  it("gives the headline as of the past time", async () => {
    history.replaceState(null, "", `/?at=${STOP_2}`);
    const { p, calls } = provider();

    render(<App provider={p} />);
    await settle();

    expect(await screen.findByText("No drift has been repaired yet.")).toBeInTheDocument();
    expect(calls.timeline).toHaveBeenCalledWith({
      start: new Date(TIMELINE.timeline.data!.start),
      end: new Date(STOP_2),
    });
  });

  it("does not poll the past every 5 s: once when the time changes, then once a minute", async () => {
    history.replaceState(null, "", `/?at=${STOP_2}`);
    const { p, calls } = provider();

    render(<App provider={p} />);
    await settle();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(calls.devices).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(calls.devices).toHaveBeenCalledTimes(2);
  });

  it("steps back one scan from now with the keyboard, and writes the time to the address", async () => {
    const { p, calls } = provider();
    render(<App provider={p} />);
    await settle();

    const slider = await screen.findByRole("slider", { name: "As of" });
    slider.focus();
    await userEvent.keyboard("{ArrowLeft}");
    await settle();

    expect(new URLSearchParams(location.search).get("at")).toBe(STOP_3);
    expect(calls.devices).toHaveBeenLastCalledWith(new Date(STOP_3));
    expect(banner()).toBeInTheDocument();
  });

  it("returns to now", async () => {
    history.replaceState(null, "", `/?at=${STOP_2}&device=spine-01`);
    render(<App provider={provider().p} />);
    await settle();

    await userEvent.click(within(banner()).getByRole("button", { name: "Return to now" }));
    await settle();

    expect(location.search).toBe("?device=spine-01");
    expect(screen.queryByRole("region", { name: "Past time" })).not.toBeInTheDocument();
    expect(device("spine-01")).toHaveAccessibleName("spine-01, spine, In sync");
  });

  it("shows a device's detail as it was then", async () => {
    history.replaceState(null, "", `/?at=${STOP_2}&device=spine-01`);
    render(<App provider={provider().p} />);
    await settle();

    const drawer = await screen.findByRole("dialog", { name: "spine-01" });
    expect(within(drawer).getByText("/interface[name=ethernet-1/1]/enabled")).toBeInTheDocument();
  });

  it("draws the lanes and the latest repair at the present", async () => {
    render(<App provider={provider().p} />);
    await settle();

    expect(await screen.findByRole("list", { name: "Drift lanes" })).toBeInTheDocument();
    expect(screen.getByText("5 s")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Past time" })).not.toBeInTheDocument();
  });

  it("says where history starts when the time is before it", async () => {
    history.replaceState(null, "", "/?at=2026-11-07T21:00:00.000Z");
    render(<App provider={provider().p} />);
    await settle();

    expect(await screen.findByText(/History starts at 2026-11-07 21:18:56 UTC/)).toBeInTheDocument();
  });

  it("says the schedule, worker, running, CI and lab panels show only the present, and lists the runs as of then", async () => {
    history.replaceState(null, "", `/?at=${STOP_2}`);
    render(<App provider={provider().p} />);
    await settle();

    expect(await screen.findByRole("region", { name: "Schedule and worker" })).toHaveTextContent(
      "Shows only the present",
    );
    expect(screen.getByRole("region", { name: "Running now" })).toHaveTextContent("Shows only the present");
    expect(screen.getByRole("region", { name: "CI" })).toHaveTextContent("Shows only the present");
    expect(screen.getByRole("region", { name: "Live lab evidence" })).toHaveTextContent("Shows only the present");
    const runs = screen.getByRole("region", { name: "Runs" });
    expect(within(runs).getAllByRole("row")).toHaveLength(RUNS_AT_SCAN_2.runs.data!.length + 1);
    expect(runs).not.toHaveTextContent("Shows only the present");
  });

  it("does not poll the loop at a past time, and says its steps are as recorded now", async () => {
    history.replaceState(null, "", `/?at=${STOP_2}`);
    const { p } = provider();
    const steps = vi.fn(p.steps);
    render(<App provider={{ ...p, steps }} />);
    await settle();

    const loop = await screen.findByRole("region", { name: "The loop" });
    // The latest run as of scan 2 is a scan still running then.
    expect(steps).toHaveBeenLastCalledWith({
      workflow_id: RUNS_AT_SCAN_2.runs.data![0]!.workflow_id,
      started_at: RUNS_AT_SCAN_2.runs.data![0]!.started_at,
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(steps).toHaveBeenCalledTimes(1);
    expect(loop).toHaveTextContent("as the audit trail records them now");
  });
});
