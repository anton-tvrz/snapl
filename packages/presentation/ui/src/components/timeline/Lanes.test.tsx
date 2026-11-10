import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ApiError } from "../../data/http";
import { domainOf } from "../../lib/lanes";
import { SCAN_STOPS, TIMELINE } from "../../test/fixtures";
import { Lanes } from "./Lanes";

const timeline = TIMELINE.timeline.data!;
const domain = domainOf(timeline);
const NOW = new Date(timeline.end);

const lanes = () => within(screen.getByRole("list", { name: "Drift lanes" })).getAllByRole("listitem");
const left = (element: Element) => parseFloat((element as HTMLElement).style.left);

describe("Lanes", () => {
  it("draws one lane per device in tier order", () => {
    render(<Lanes timeline={timeline} domain={domain} now={NOW} />);

    expect(lanes().map((lane) => lane.getAttribute("aria-label"))).toEqual([
      "spine-01",
      "spine-02",
      "leaf-01",
      "leaf-02",
      "leaf-03",
      "leaf-04",
    ]);
  });

  it("says in words what each lane shows: spine-01 drifted, then in sync", () => {
    render(<Lanes timeline={timeline} domain={domain} now={NOW} />);
    const spine = lanes()[0]!;

    expect(spine).toHaveTextContent(
      "spine-01: Missing intent from 21:20:13 UTC (2 scans), then In sync from 21:20:18 UTC (1 scan)",
    );
  });

  it("shows leaf-01 undesired throughout", () => {
    render(<Lanes timeline={timeline} domain={domain} now={NOW} />);
    const leaf = lanes()[2]!;

    expect(leaf).toHaveTextContent("leaf-01: Undesired config from 21:20:13 UTC (3 scans)");
    const drawn = leaf.querySelectorAll("[data-state]");
    // Before its first scan there is no data; from then on, one stretch per scan.
    expect([...drawn].map((s) => s.getAttribute("data-state"))).toEqual(["no_data", "undesired", "undesired", "undesired"]);
  });

  it("marks each scan with its state's shape, not colour alone", () => {
    render(<Lanes timeline={timeline} domain={domain} now={NOW} />);
    const marks = lanes()[0]!.querySelectorAll("[data-scan]");

    expect([...marks].map((m) => m.getAttribute("data-shape"))).toEqual(["triangle", "triangle", "circle"]);
  });

  it("marks the reconcile between scans 2 and 3", () => {
    render(<Lanes timeline={timeline} domain={domain} now={NOW} />);
    const marker = within(screen.getByRole("list", { name: "Repair runs" })).getByRole("listitem");
    const scans = lanes()[0]!.querySelectorAll("[data-scan]");

    expect(marker).toHaveAccessibleName("Reconcile, started by an operator at 21:20:15 UTC");
    expect(left(marker)).toBeGreaterThan(left(scans[1]!));
    expect(left(marker)).toBeLessThan(left(scans[2]!));
  });

  it("draws the cursor at the shown time", () => {
    const { container } = render(<Lanes timeline={timeline} domain={domain} now={NOW} cursor={new Date(SCAN_STOPS[1]!)} />);
    const cursor = container.querySelector("[data-cursor]")!;
    const scans = lanes()[0]!.querySelectorAll("[data-scan]");

    expect(left(cursor)).toBeGreaterThan(left(scans[1]!));
    expect(left(cursor)).toBeLessThan(left(scans[2]!));
  });

  it("labels a device no longer in intent", () => {
    const gone = { ...timeline, lanes: [{ ...timeline.lanes[0]!, name: "old-leaf", in_intent: false }] };
    render(<Lanes timeline={gone} domain={domain} now={NOW} />);

    expect(lanes()[0]).toHaveTextContent("no longer in intent");
  });

  it("gives the command that fills empty lanes", () => {
    render(<Lanes timeline={{ ...timeline, lanes: [], markers: [] }} domain={domain} now={NOW} />);

    expect(screen.getByText("No scan recorded in this range.")).toBeInTheDocument();
    expect(screen.getByText("uv run snapl scan")).toBeInTheDocument();
  });

  it("shows a refused range with the server's reason and keeps the lanes", () => {
    const error = new ApiError(422, "the range holds 24,000 scan points, more than 20,000: ask for a shorter range");
    render(<Lanes timeline={timeline} domain={domain} now={NOW} error={error} />);

    expect(screen.getByRole("alert")).toHaveTextContent("ask for a shorter range");
    expect(lanes()).toHaveLength(6);
  });
});
