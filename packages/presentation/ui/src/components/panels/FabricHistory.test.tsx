import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { HistoryResponse, Point, Series } from "../../api/types";
import { FABRIC_HISTORY } from "../../test/fixtures";
import { FabricHistory } from "./FabricHistory";

const START = "2026-11-07T00:00:00Z";
const END = "2026-11-08T00:00:00Z";

function series(name: Series["name"], points: Point[], bucket: Series["bucket"] = null): Series {
  return { name, start: START, end: END, bucket, points, markers: [] };
}

function history(parts: Partial<Record<Series["name"], Point[]>>): HistoryResponse {
  return {
    contract_version: "1",
    generated_at: END,
    use_case: "dcfabric",
    series: [
      series("in_sync_devices", parts.in_sync_devices ?? []),
      series("open_drifts", parts.open_drifts ?? []),
      series("runs", parts.runs ?? [{ t: START, value: null, by_outcome: {} }], "1h"),
      series("repair_duration", parts.repair_duration ?? []),
    ],
  } as HistoryResponse;
}

const QUIET = history({});

describe("FabricHistory", () => {
  it("draws the four series of the demo arc, each with a text alternative that says what it shows", () => {
    render(<FabricHistory history={FABRIC_HISTORY} inIntent={6} />);

    expect(screen.getByRole("img", { name: /^Devices in sync at each scan, as a chart: 3 scans, from 4 to 5 in sync, against 6 in intent now\. The latest scan found 5\./ })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /^Open drifts after each scan, as a chart: 3 scans, at most 2 open\. The latest scan left 1 open\./ })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /^Runs started in each hour, by trigger, as a chart: 12 runs, none failed\./ })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /^Each repaired drift's duration, scan to scan, as a chart: 1 repair, the longest 5 s\./ })).toBeInTheDocument();
  });

  it("puts each chart's numbers in a table behind a closed toggle", () => {
    render(<FabricHistory history={FABRIC_HISTORY} inIntent={6} />);

    const inSync = screen.getByRole("table", { name: "Devices in sync at each scan" });
    expect(inSync.closest("details")).not.toHaveAttribute("open");
    expect(within(inSync).getAllByRole("row").slice(1).map((row) => row.textContent)).toEqual([
      "2026-11-07 21:20:185",
      "2026-11-07 21:20:154",
      "2026-11-07 21:20:134",
    ]);
    expect(within(screen.getByRole("table", { name: "Open drifts after each scan" })).getAllByRole("row")).toHaveLength(4);
    expect(within(screen.getByRole("table", { name: "Repaired drifts, scan to scan" })).getByText("5 s")).toBeInTheDocument();
  });

  it("says each bucket's runs in words in the runs table, and 'no runs' for a quiet one", () => {
    render(<FabricHistory history={FABRIC_HISTORY} inIntent={6} />);

    const rows = within(screen.getByRole("table", { name: "Runs by hour" })).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(25);
    expect(rows[0]).toHaveTextContent("Operator: 12 succeeded.");
    expect(rows[1]).toHaveTextContent("no runs");
  });

  it("names the triggers and marks failures and running runs by shape and word in the runs legend", () => {
    const runs = [{ t: START, value: 4, by_outcome: { "schedule.succeeded": 1, "schedule.failed": 1, "operator.running": 1, "unknown.succeeded": 1 } }];
    render(<FabricHistory history={history({ runs })} inIntent={6} />);

    const legend = screen.getByRole("list", { name: "What the runs chart shows" });
    expect(within(legend).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "Scheduled",
      "Operator",
      "Other",
      "✕ Failed (hatched)",
      "… Running (outlined)",
    ]);
    expect(screen.getByRole("img", { name: /: 4 runs, 1 failed, 1 running\./ })).toBeInTheDocument();
  });

  it("says so in words, never with zeros, when the range has no scan, no run and no repair", () => {
    render(<FabricHistory history={QUIET} inIntent={6} />);

    expect(screen.getAllByText("No scans in this range.")).toHaveLength(2);
    expect(screen.getByText("No runs in this range.")).toBeInTheDocument();
    expect(screen.getByText("No drift repaired in this range.")).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("leaves out the devices in intent when they are not known", () => {
    render(<FabricHistory history={FABRIC_HISTORY} inIntent={undefined} />);

    expect(screen.getByRole("img", { name: /^Devices in sync at each scan, as a chart: 3 scans, from 4 to 5 in sync\. / })).toBeInTheDocument();
  });

  it("lists only the latest 100 scans in a table over a long range, and says how many it left out", () => {
    const points = Array.from({ length: 250 }, (_, i) => ({ t: new Date(Date.parse(START) + i * 60_000).toISOString(), value: 6 }));
    render(<FabricHistory history={history({ in_sync_devices: points, open_drifts: points })} inIntent={6} />);

    const table = screen.getByRole("table", { name: "Devices in sync at each scan" });
    expect(within(table).getAllByRole("row")).toHaveLength(101);
    expect(screen.getAllByText("The latest 100 of 250 scans.")).toHaveLength(2);
  });

  it("says a snapshot made before these series does not hold them", () => {
    render(<FabricHistory history={{ ...QUIET, series: [] }} inIntent={6} />);

    expect(screen.getByText("Not in this recording: it was made before the fabric history existed.")).toBeInTheDocument();
    expect(screen.queryByText("No scans in this range.")).not.toBeInTheDocument();
  });

  it("says why the history is unavailable, and that it is loading before any answer", () => {
    const { rerender } = render(<FabricHistory history={undefined} inIntent={6} />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();

    rerender(<FabricHistory history={undefined} inIntent={6} error={new Error("422: range too long")} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Fabric history unavailable: 422: range too long");
  });
});
