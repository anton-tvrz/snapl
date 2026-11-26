import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ApiError } from "../../data/http";
import { HISTORY, NOW } from "../../test/fixtures";
import { History } from "./History";

const rows = () => within(screen.getByRole("table", { name: "Drift and deploys by hour" })).getAllByRole("row").slice(1);

describe("History", () => {
  it("gives one row per hour, newest first, with the scans, the most drifted, deploys and repairs", () => {
    render(<History history={HISTORY} now={NOW} />);

    expect(rows()).toHaveLength(25);
    expect(rows()[0]).toHaveTextContent("2026-11-07 21:00");
    expect([...(rows()[0] as HTMLTableRowElement).cells].map((cell) => cell.textContent)).toEqual([
      "2026-11-07 21:00",
      "3",
      "2",
      "1",
      "8 / 0",
      "0 / 0",
    ]);
  });

  it("says no scans for a quiet hour, rather than zeros", () => {
    render(<History history={HISTORY} now={NOW} />);

    expect(rows()[1]).toHaveTextContent("no scans");
  });

  it("says where the audit trail starts, and lists the repair runs", () => {
    const { container } = render(<History history={HISTORY} now={NOW} />);

    expect(container).toHaveTextContent("The audit trail starts 1 min ago.");
    expect(screen.getByRole("list", { name: "Repair runs in this range" })).toHaveTextContent(
      "33 s ago: reconcile reconcile-217b6000-0e6c-4919-81d9-9f6a1075cd1d (operator)",
    );
  });

  // spec 014 FR-020, FR-021: a chart, with the table one click away.
  it("draws the history as a chart whose text alternative is the table", () => {
    render(<History history={HISTORY} now={NOW} />);

    const chart = screen.getByRole("img", { name: /Drift and deploys by hour, as a chart/ });
    expect(chart).toHaveAccessibleName(/The same numbers are in the table below/);
  });

  it("keeps the table behind a closed toggle, with every column it had", () => {
    render(<History history={HISTORY} now={NOW} />);

    const toggle = screen.getByText("Show the table");
    const details = toggle.closest("details")!;
    expect(details).not.toHaveAttribute("open");
    const table = within(details).getByRole("table", { name: "Drift and deploys by hour" });
    expect(within(table).getAllByRole("columnheader").map((th) => th.textContent)).toEqual([
      "Start (UTC)",
      "Scans",
      "Most drifted devices",
      "Most undesired paths",
      "Deploys ok / failed",
      "Repairs ok / failed",
    ]);
  });

  it("names every mark in a legend, by word and not by colour alone", () => {
    render(<History history={HISTORY} now={NOW} />);

    const legend = screen.getByRole("list", { name: "What the chart shows" });
    expect(within(legend).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "Scans",
      "Most drifted devices (line)",
      "✓ Deploys that succeeded",
      "✕ Deploys that failed (hatched)",
      "✓ Scheduled repairs that succeeded (outlined)",
      "✕ Scheduled repairs that failed (outlined, hatched)",
    ]);
  });

  it("says so in words when no bucket in the range has a scan or a deploy", () => {
    const quiet = {
      ...HISTORY,
      series: HISTORY.series.map((series) => ({
        ...series,
        points: series.bucket ? series.points.map((point) => ({ ...point, by_outcome: null })) : [],
      })),
    } as typeof HISTORY;
    render(<History history={quiet} now={NOW} />);

    expect(screen.getByText("No scans or deploys in this range.")).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("says why the history is unavailable", () => {
    render(<History history={undefined} now={NOW} error={new ApiError(422, "the range holds more than 2,000 buckets")} />);

    expect(screen.getByRole("alert")).toHaveTextContent("History unavailable: the range holds more than 2,000 buckets");
  });
});
