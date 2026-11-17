import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { FabricSummary as Summary } from "../../lib/summary";
import { FabricSummary } from "./FabricSummary";

const SOME: Summary = {
  inIntent: 6,
  inSync: 4,
  scanned: 6,
  drifted: [
    { name: "spine-01", state: "missing_and_undesired", paths: 4, share: 1 },
    { name: "leaf-01", state: "undesired", paths: 1, share: 0.25 },
  ],
};

describe("FabricSummary", () => {
  it("draws the share in sync as a ring, with the count in words inside and for screen readers", () => {
    render(<FabricSummary summary={SOME} onSelect={vi.fn()} />);

    const ring = screen.getByRole("img", { name: "4 of 6 devices in sync" });
    expect(ring.querySelector("[data-arc]")).toHaveAttribute("data-share", "0.667");
    expect(screen.getByText("4 of 6")).toHaveClass("text-number");
    expect(screen.getByText("in sync")).toBeInTheDocument();
  });

  it("draws a whole ring when every device is in sync, and says no drift is open", () => {
    render(<FabricSummary summary={{ inIntent: 6, inSync: 6, scanned: 6, drifted: [] }} onSelect={vi.fn()} />);

    expect(screen.getByRole("img", { name: "6 of 6 devices in sync" }).querySelector("[data-arc]")).toHaveAttribute("data-share", "1.000");
    expect(screen.getByText("No drift open.")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Drifted devices, most paths first" })).not.toBeInTheDocument();
  });

  it("draws the dashed never-scanned ring, with no arc, when nothing has been scanned", () => {
    render(<FabricSummary summary={{ inIntent: 6, inSync: 0, scanned: 0, drifted: [] }} onSelect={vi.fn()} />);

    const ring = screen.getByRole("img", { name: "No device has been scanned yet" });
    expect(ring.querySelector("[data-arc]")).toBeNull();
    expect(ring.querySelector("[data-track]")).toHaveAttribute("stroke-dasharray");
    expect(screen.getByText("No scan yet.")).toBeInTheDocument();
  });

  it("says so, and draws no ring, when no device is in intent", () => {
    render(<FabricSummary summary={{ inIntent: 0, inSync: 0, scanned: 0, drifted: [] }} onSelect={vi.fn()} />);

    expect(screen.getByText("No device is in intent.")).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("ranks the drifted devices, each with its state's mark and label, its count and a bar", () => {
    render(<FabricSummary summary={SOME} onSelect={vi.fn()} />);

    const rows = within(screen.getByRole("list", { name: "Drifted devices, most paths first" })).getAllByRole("listitem");
    expect(rows.map((row) => within(row).getByRole("button").getAttribute("aria-label"))).toEqual([
      "Open spine-01: Missing and undesired, 4 paths differ",
      "Open leaf-01: Undesired config, 1 path differs",
    ]);
    expect(rows[0]!.querySelector("[data-shape]")).toHaveAttribute("data-shape", "diamond");
    expect(rows[1]!.querySelector("[data-shape]")).toHaveAttribute("data-shape", "square");
    expect((rows[0]!.querySelector("[data-bar]") as HTMLElement).style.width).toBe("100%");
    expect((rows[1]!.querySelector("[data-bar]") as HTMLElement).style.width).toBe("25%");
  });

  it("lists a scan error with its label and no bar", () => {
    const summary = { ...SOME, drifted: [{ name: "leaf-09", state: "error", paths: 0, share: null }] };
    render(<FabricSummary summary={summary} onSelect={vi.fn()} />);

    const row = screen.getByRole("button", { name: "Open leaf-09: Scan error" });
    expect(row.querySelector("[data-bar]")).toBeNull();
  });

  it("opens a device when its row is chosen", async () => {
    const onSelect = vi.fn();
    render(<FabricSummary summary={SOME} onSelect={onSelect} />);

    await userEvent.click(screen.getByRole("button", { name: /^Open leaf-01:/ }));

    expect(onSelect).toHaveBeenCalledWith("leaf-01");
  });
});
