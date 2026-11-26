import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "../../data/http";
import { domainOf } from "../../lib/lanes";
import type { FabricHealth, View } from "../../api/types";
import { HEALTH, SCAN_STOPS, TIMELINE } from "../../test/fixtures";
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

    expect(marker).toHaveTextContent("Reconcile, started by an operator at 21:20:15 UTC");
    expect(left(marker)).toBeGreaterThan(left(scans[1]!));
    expect(left(marker)).toBeLessThan(left(scans[2]!));
  });

  it("shows a marker's run as the loop when it is chosen", async () => {
    const onSelectRun = vi.fn();
    render(<Lanes timeline={timeline} domain={domain} now={NOW} onSelectRun={onSelectRun} />);

    await userEvent.click(screen.getByRole("button", { name: "Reconcile, started by an operator at 21:20:15 UTC" }));

    expect(onSelectRun).toHaveBeenCalledWith({
      workflow_id: "reconcile-217b6000-0e6c-4919-81d9-9f6a1075cd1d",
      started_at: "2026-11-07T21:20:15.472996Z",
    });
  });

  it("draws the cursor at the shown time", () => {
    const { container } = render(<Lanes timeline={timeline} domain={domain} now={NOW} cursor={new Date(SCAN_STOPS[1]!)} />);
    const cursor = container.querySelector("[data-cursor]")!;
    const scans = lanes()[0]!.querySelectorAll("[data-scan]");

    expect(left(cursor)).toBeGreaterThan(left(scans[1]!));
    expect(left(cursor)).toBeLessThan(left(scans[2]!));
  });

  // spec 014 FR-018: devices that left intent do not take the panel over.
  const withGone = (count: number) => ({
    ...timeline,
    lanes: [
      ...timeline.lanes,
      ...Array.from({ length: count }, (_, n) => ({
        ...timeline.lanes[0]!,
        device_id: `1111111${n}-1111-1111-1111-111111111111`,
        name: `old-leaf-${n}`,
        in_intent: false,
      })),
    ],
  });
  const folded = () => screen.getByRole("group", { name: /no longer in intent/ });

  it("draws only the devices in intent, and folds the others into one closed row with their count", () => {
    render(<Lanes timeline={withGone(18)} domain={domain} now={NOW} />);

    expect(lanes().map((lane) => lane.getAttribute("aria-label"))).toEqual(timeline.lanes.map((lane) => lane.name));
    expect(folded()).not.toHaveAttribute("open");
    expect(within(folded()).getByText("18 devices no longer in intent")).toBeInTheDocument();
  });

  it("shows every folded lane, labelled, when the row is opened", async () => {
    render(<Lanes timeline={withGone(2)} domain={domain} now={NOW} />);

    await userEvent.click(within(folded()).getByText("2 devices no longer in intent"));

    expect(folded()).toHaveAttribute("open");
    const gone = within(screen.getByRole("list", { name: "Drift lanes of devices no longer in intent" })).getAllByRole("listitem");
    expect(gone.map((lane) => lane.getAttribute("aria-label"))).toEqual(["old-leaf-0", "old-leaf-1"]);
    expect(gone[0]).toHaveTextContent("no longer in intent");
  });

  it("opens the row when the selected device is one of them", () => {
    render(<Lanes timeline={withGone(2)} domain={domain} now={NOW} selected="old-leaf-1" />);

    expect(folded()).toHaveAttribute("open");
  });

  it("says one device in the singular", () => {
    render(<Lanes timeline={withGone(1)} domain={domain} now={NOW} />);

    expect(within(folded()).getByText("1 device no longer in intent")).toBeInTheDocument();
  });

  it("has no folded row when every device is in intent", () => {
    render(<Lanes timeline={timeline} domain={domain} now={NOW} />);

    expect(screen.queryByRole("group", { name: /no longer in intent/ })).not.toBeInTheDocument();
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

  describe("health (spec 016)", () => {
    // The arc with today's lab health: leaf-01 unhealthy 18:50:48 to 19:50:48,
    // both spines unhealthy since 20:20:48 and still now. The domain reaches
    // back to 18:00, as the page's does once it takes the health changes in.
    const health = HEALTH.health as View<FabricHealth>;
    const wide = { start: new Date("2026-11-07T18:00:00Z"), end: domain.end };
    const span = wide.end.getTime() - wide.start.getTime();
    const lane = (name: string) => lanes().find((item) => item.getAttribute("aria-label") === name)!;
    const stretches = (name: string) => [...lane(name).querySelectorAll<HTMLElement>("[data-health]")];
    const width = (element: HTMLElement) => parseFloat(element.style.width);
    const key = () => screen.getByText(/· underline: unhealthy/);

    function drawWith(over: Partial<Parameters<typeof Lanes>[0]> = {}) {
      return render(<Lanes timeline={timeline} domain={wide} now={NOW} health={health} {...over} />);
    }

    it("draws each unhealthy stretch under the device's own drift lane, on its axis", () => {
      drawWith();

      const [leaf] = stretches("leaf-01");
      expect(leaf!.dataset.health).toBe("closed");
      expect(left(leaf!)).toBeCloseTo(((Date.parse("2026-11-07T18:50:48.919877Z") - wide.start.getTime()) / span) * 100, 3);
      expect(width(leaf!)).toBeCloseTo((3_600_000 / span) * 100, 3);
      expect(stretches("leaf-02")).toEqual([]);
    });

    it("draws a stretch still open up to now", () => {
      drawWith();

      const [spine] = stretches("spine-01");
      expect(spine!.dataset.health).toBe("open");
      expect(left(spine!) + width(spine!)).toBeCloseTo(100, 3);
    });

    it("leaves the drift drawing as it is", () => {
      drawWith();
      const drift = (name: string) => [...lane(name).querySelectorAll("[data-state]")].map((s) => s.getAttribute("data-state"));
      const without = drift("spine-01");

      cleanup();
      render(<Lanes timeline={timeline} domain={wide} now={NOW} />);
      expect(drift("spine-01")).toEqual(without);
    });

    it("titles each stretch with its time and reasons, and says it in the lane's words", () => {
      drawWith();

      expect(stretches("leaf-01")[0]!.title).toBe("Unhealthy 18:50:48 UTC to 19:50:48 UTC: ethernet-1/49 down (expected up)");
      expect(lane("leaf-01")).toHaveTextContent(/^leaf-01leaf-01: [^;]*; unhealthy 18:50:48 UTC to 19:50:48 UTC/);
      expect(lane("spine-01")).toHaveTextContent(/^spine-01spine-01: [^;]*; unhealthy from 20:20:48 UTC, still now/);
      expect(lane("leaf-02")).not.toHaveTextContent(/unhealthy/);
    });

    it("clips a stretch that began before the drawing to its start", () => {
      drawWith({ domain: { start: new Date("2026-11-07T19:00:00Z"), end: domain.end } });

      expect(left(stretches("leaf-01")[0]!)).toBe(0);
    });

    it("explains the underline in the lanes' key while one is drawn", () => {
      drawWith();
      expect(key()).toBeInTheDocument();

      cleanup();
      const devices = health.data!.devices.map((device) => ({ ...device, episodes: [] }));
      drawWith({ health: { ...health, data: { ...health.data!, devices } } });
      expect(screen.queryByText(/underline: unhealthy/)).not.toBeInTheDocument();
    });

    it("says in the key why the health cannot be shown", () => {
      drawWith({ health: { source: "audit", available: false, reason: "not in this snapshot", data: null } });

      expect(screen.getByText(/· health: not in this snapshot/)).toBeInTheDocument();
      expect(document.querySelectorAll("[data-health]")).toHaveLength(0);
    });

    it("draws no health before the first answer", () => {
      render(<Lanes timeline={timeline} domain={wide} now={NOW} />);

      expect(document.querySelectorAll("[data-health]")).toHaveLength(0);
      expect(screen.queryByText(/health/)).not.toBeInTheDocument();
    });
  });
});
