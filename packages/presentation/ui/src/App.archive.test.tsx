import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import type { DataProvider } from "./data/provider";
import {
  HISTORY,
  LINKS,
  METRICS,
  MODE_ARCHIVE,
  OVERVIEW_ARCHIVE,
  SOURCES,
  STEPS_DEPLOY,
  TIMELINE,
  TOPOLOGY,
} from "./test/fixtures";

// The page on `snapl dashboard --archive` (spec 011 US5, T073, T077).

function provider() {
  const calls = {
    overview: vi.fn(async () => OVERVIEW_ARCHIVE),
    metrics: vi.fn(async (view: keyof typeof METRICS) => METRICS[view]),
    timeline: vi.fn(async () => TIMELINE),
  };
  const p = {
    mode: async () => MODE_ARCHIVE,
    sources: async () => SOURCES,
    overview: calls.overview,
    topology: async () => TOPOLOGY,
    links: async () => LINKS,
    devices: async () => {
      throw new Error("not asked");
    },
    runs: async () => {
      throw new Error("not asked");
    },
    timeline: calls.timeline,
    history: async () => HISTORY,
    steps: async () => STEPS_DEPLOY,
    metrics: calls.metrics,
  } as unknown as DataProvider;
  return { p, calls };
}

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

describe("App on an archive", () => {
  it("names the archive in a banner, and nothing is labelled live", async () => {
    render(<App provider={provider().p} />);
    await settle();

    expect(await screen.findByRole("region", { name: "Archive" })).toHaveTextContent(MODE_ARCHIVE.archive!.name);
    expect(within(screen.getAllByRole("banner")[0]!).getByText("Archive")).toBeInTheDocument();
    expect(screen.queryByText("Live")).not.toBeInTheDocument();
    expect(screen.queryByText(/Following now/)).not.toBeInTheDocument();
  });

  it("shows what the archive does not hold neutrally", async () => {
    render(<App provider={provider().p} />);
    await settle();

    const running = await screen.findByRole("region", { name: "Running now" });
    expect(within(running).getByRole("status")).toHaveTextContent("Not in this archive");
    expect(running).not.toHaveTextContent("Unavailable");
  });

  it("does not poll: an archive does not change", async () => {
    const { p, calls } = provider();
    render(<App provider={p} />);
    await settle();
    await screen.findByRole("region", { name: "Archive" });
    const before = { overview: calls.overview.mock.calls.length, metrics: calls.metrics.mock.calls.length };

    await act(async () => {
      await vi.advanceTimersByTimeAsync(120_000);
    });

    expect(calls.overview).toHaveBeenCalledTimes(before.overview);
    expect(calls.metrics).toHaveBeenCalledTimes(before.metrics);
  });

  it("asks the metrics for the exported range only, whatever range the lanes use", async () => {
    const { p, calls } = provider();
    render(<App provider={p} />);
    await settle();
    await screen.findByRole("region", { name: "Archive" });

    await userEvent.click(screen.getByRole("button", { name: "24 h" }));
    await settle();

    expect(calls.timeline).toHaveBeenLastCalledWith(expect.objectContaining({ end: new Date(MODE_ARCHIVE.now) }));
    for (const call of calls.metrics.mock.calls) expect(call).toEqual([expect.any(String), undefined]);
    expect(screen.getByText(/an archive holds no other/)).toBeInTheDocument();
  });
});
