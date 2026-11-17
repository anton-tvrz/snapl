import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { DeviceDriftSummary, DriftSummary, View } from "../../api/types";
import { DRIFT_SUMMARY, NOW } from "../../test/fixtures";
import { DriftByDevice } from "./DriftByDevice";

function device(name: string, fields: Partial<DeviceDriftSummary> = {}): DeviceDriftSummary {
  return {
    device_id: `id-${name}`,
    name,
    in_intent: true,
    episodes: 1,
    open: 0,
    drifted_s: 60,
    most_missing: 1,
    most_undesired: 0,
    ...fields,
  };
}

function view(devices: DeviceDriftSummary[]): View<DriftSummary> {
  return {
    source: "audit",
    available: true,
    stale: false,
    fetched_at: NOW.toISOString(),
    data: { start: "2026-11-06T21:20:48Z", end: "2026-11-07T21:20:48Z", devices },
  };
}

const rows = () => within(screen.getByRole("list", { name: "Devices that drifted, most drifts first" })).getAllByRole("listitem");

describe("DriftByDevice", () => {
  it("ranks the devices as the server orders them, each with its drifts, time drifted and most paths in words", () => {
    const summary = view([
      device("leaf-01", { episodes: 3, open: 1, drifted_s: 754, most_missing: 2, most_undesired: 1 }),
      device("spine-01", { drifted_s: 47 }),
    ]);
    render(<DriftByDevice summary={summary} now={NOW} onSelect={vi.fn()} />);

    expect(rows().map((row) => within(row).getByRole("button").getAttribute("aria-label"))).toEqual([
      "Open leaf-01: 3 drifts, 1 still open, drifted 12 min 34 s, at most 2 paths missing and 1 undesired",
      "Open spine-01: 1 drift, drifted 47 s, at most 1 path missing and 0 undesired",
    ]);
    expect(within(rows()[0]!).getByText("12 min 34 s")).toBeInTheDocument();
  });

  it("draws each device's time drifted as a bar, a share of the longest, in ink", () => {
    render(<DriftByDevice summary={view([device("leaf-01", { drifted_s: 200 }), device("spine-01", { drifted_s: 50 })])} now={NOW} onSelect={vi.fn()} />);

    const bars = rows().map((row) => row.querySelector<HTMLElement>("[data-bar]")!);
    expect(bars.map((bar) => bar.style.width)).toEqual(["100%", "25%"]);
    expect(bars[0]).toHaveClass("bg-line-strong");
  });

  it("draws empty bars, and still lists the drift, when every drift was seen by one scan only", () => {
    // An open drift seen by one scan has drifted 0 s, scan to scan (spec 015 PR B).
    render(<DriftByDevice summary={view([device("leaf-01", { open: 1, drifted_s: 0 })])} now={NOW} onSelect={vi.fn()} />);

    expect(within(rows()[0]!).getByRole("button")).toHaveAccessibleName(
      "Open leaf-01: 1 drift, 1 still open, seen by one scan so far, at most 1 path missing and 0 undesired",
    );
    expect(rows()[0]!.querySelector<HTMLElement>("[data-bar]")!.style.width).toBe("0%");
  });

  it("says the undesired count is unknown for scans recorded before the split, never 0", () => {
    render(<DriftByDevice summary={view([device("leaf-01", { most_undesired: null })])} now={NOW} onSelect={vi.fn()} />);

    expect(within(rows()[0]!).getByRole("button").getAttribute("aria-label")).toMatch(
      /at most 1 path missing; undesired not recorded$/,
    );
  });

  it("opens a device's detail from its row", async () => {
    const onSelect = vi.fn();
    render(<DriftByDevice summary={view([device("leaf-01")])} now={NOW} onSelect={onSelect} />);

    await userEvent.click(within(rows()[0]!).getByRole("button"));

    expect(onSelect).toHaveBeenCalledWith("leaf-01");
  });

  it("lists a device no longer in intent without a link to a detail it no longer has", () => {
    render(<DriftByDevice summary={view([device("leaf-09", { in_intent: false })])} now={NOW} onSelect={vi.fn()} />);

    expect(within(rows()[0]!).queryByRole("button")).not.toBeInTheDocument();
    expect(within(rows()[0]!).getByText("no longer in intent")).toBeInTheDocument();
  });

  it("says when nothing drifted in the range", () => {
    render(<DriftByDevice summary={view([])} now={NOW} onSelect={vi.fn()} />);

    expect(screen.getByText("No drift in this range.")).toBeInTheDocument();
  });

  it("reads the server's answer on the demo arc", () => {
    render(<DriftByDevice summary={DRIFT_SUMMARY.summary} now={NOW} onSelect={vi.fn()} />);

    expect(rows().map((row) => within(row).getByRole("button").getAttribute("aria-label"))).toEqual([
      "Open leaf-01: 1 drift, 1 still open, drifted 5 s, at most 0 paths missing and 1 undesired",
      "Open spine-01: 1 drift, drifted 5 s, at most 1 path missing and 0 undesired",
    ]);
    expect(screen.getByText(/^Audit trail/)).toBeInTheDocument();
  });

  it("gives the reason and the command when the audit trail cannot be read", () => {
    const down: View<DriftSummary> = { source: "audit", available: false, reason: "unable to open database file", remedy: "snapl scan" };
    render(<DriftByDevice summary={down} now={NOW} onSelect={vi.fn()} />);

    expect(screen.getByText(/unable to open database file/)).toBeInTheDocument();
    expect(screen.getByText("snapl scan")).toBeInTheDocument();
  });
});
