import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { InlineProvider, type SnapshotBundle } from "./data/inline";
import { SNAPSHOT_BUNDLE } from "./test/fixtures";

// Every folded panel and group open (#290).
const ALL_OPEN = "/?open=history,fabric-history,drift-by-device,metrics-timing,metrics-work,metrics-worker,metrics-dashboard";

// The page in a snapshot file (spec 011 US6, T082).

const bundle = SNAPSHOT_BUNDLE as SnapshotBundle;

function provider() {
  const inline = new InlineProvider(bundle);
  const overview = vi.fn(inline.overview);
  const metrics = vi.fn(inline.metrics);
  return { p: Object.assign(Object.create(inline) as InlineProvider, { ...inline, overview, metrics }), overview, metrics };
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

describe("App in a snapshot", () => {
  it("says it is a recording: when it was made, from which commit, and the range", async () => {
    render(<App provider={provider().p} />);

    const banner = await screen.findByRole("region", { name: "Recording" });
    expect(banner).toHaveTextContent("A recording made 2026-11-07 21:20:48 UTC from commit 6baff39");
    expect(banner).toHaveTextContent("covering 2026-11-06 21:20:48 UTC to 2026-11-07 21:20:48 UTC");
    expect(banner).toHaveTextContent("Nothing here is live, and nothing refreshes.");
    expect(within(screen.getAllByRole("banner")[0]!).getByText("Recording")).toBeInTheDocument();
    expect(screen.queryByText("Live")).not.toBeInTheDocument();
  });

  it("has the number row, from the bundle and with no request of its own", async () => {
    render(<App provider={provider().p} />);

    const row = await screen.findByRole("list", { name: "The fabric in numbers" });
    expect(within(row).getAllByRole("listitem")).toHaveLength(4);
    expect(await within(row).findByText(/of \d+$/)).toBeInTheDocument();
  });

  it("has the fabric history, from the bundle", async () => {
    history.replaceState(null, "", ALL_OPEN);
    render(<App provider={provider().p} />);

    const panel = await screen.findByRole("region", { name: "Fabric history" });
    expect(await within(panel).findByRole("img", { name: /^Devices in sync at each scan, as a chart/ })).toBeInTheDocument();
    expect(within(panel).getByRole("img", { name: /^Runs started in each hour/ })).toBeInTheDocument();
  });

  it("has drift by device, from the bundle", async () => {
    history.replaceState(null, "", ALL_OPEN);
    render(<App provider={provider().p} />);

    const panel = await screen.findByRole("region", { name: "Drift by device" });
    expect(await within(panel).findByRole("button", { name: /^Open leaf-01:/ })).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: /^Open spine-01:/ })).toBeInTheDocument();
  });

  it("counts the runs in the fourth card from a one-day bundle", async () => {
    render(<App provider={provider().p} />);

    const row = await screen.findByRole("list", { name: "The fabric in numbers" });
    const card = within(row).getByRole("heading", { name: "Runs, 24 h" }).closest("li")!;
    expect(await within(card).findByText("12")).toBeInTheDocument();
    expect(card).toHaveTextContent("None failed.");
  });

  it("leaves the runs count out of a bundle that covers a week, rather than showing the week as 24 hours", async () => {
    const week = new Date(Date.parse(bundle.manifest.end) - 7 * 86_400_000).toISOString();
    const fabric = bundle.fabric_history!;
    const weekly = { ...bundle, fabric_history: { ...fabric, series: fabric.series.map((one) => ({ ...one, start: week })) } };
    render(<App provider={new InlineProvider(weekly)} />);

    const row = await screen.findByRole("list", { name: "The fabric in numbers" });
    const card = within(row).getByRole("heading", { name: "Runs, 24 h" }).closest("li")!;
    expect(await within(card).findByText("This answer covers 7 d, not 24 h: see the fabric history.")).toBeInTheDocument();
  });

  it("asks nothing twice", async () => {
    const { p, overview, metrics } = provider();
    render(<App provider={p} />);
    await screen.findByRole("region", { name: "Recording" });
    await settle();
    const before = [overview.mock.calls.length, metrics.mock.calls.length];

    await act(async () => {
      await vi.advanceTimersByTimeAsync(120_000);
    });

    expect([overview.mock.calls.length, metrics.mock.calls.length]).toEqual(before);
  });

  it("shows commands as what the operator could run, with nothing to copy", async () => {
    render(<App provider={provider().p} />);

    const ci = await screen.findByRole("region", { name: "CI" });
    expect(await within(ci).findByText("export GITHUB_TOKEN=…")).toBeInTheDocument();
    expect(ci).toHaveTextContent("The operator could run: export GITHUB_TOKEN=…");
    expect(screen.queryByRole("button", { name: /^Copy/ })).not.toBeInTheDocument();
  });

  it("has no range control: it holds one range", async () => {
    render(<App provider={provider().p} />);
    await screen.findByRole("region", { name: "Recording" });

    expect(screen.queryByRole("region", { name: "Time range" })).not.toBeInTheDocument();
  });

  it("replays the drift over the bundle's range", async () => {
    render(<App provider={provider().p} />);
    const slider = await screen.findByRole("slider", { name: "As of" });

    slider.focus();
    await userEvent.keyboard("{ArrowLeft}{ArrowLeft}");
    await settle();

    expect(await screen.findByRole("button", { name: "spine-01, spine, Missing intent" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Past time" })).toHaveTextContent("2026-11-07 21:20:15 UTC");
  });
});
