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

  it("says why the history is unavailable", () => {
    render(<History history={undefined} now={NOW} error={new ApiError(422, "the range holds more than 2,000 buckets")} />);

    expect(screen.getByRole("alert")).toHaveTextContent("History unavailable: the range holds more than 2,000 buckets");
  });
});
