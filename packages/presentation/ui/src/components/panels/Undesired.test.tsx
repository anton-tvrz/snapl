import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { DeviceView, View } from "../../api/types";
import { DEVICES, NOW } from "../../test/fixtures";
import { Undesired } from "./Undesired";

const devices = DEVICES.devices as View<DeviceView[]>;

describe("Undesired", () => {
  it("lists every undesired path across the fabric, grouped by device", () => {
    const extra = devices.data!.map((d) => (d.name === "leaf-03" ? { ...d, undesired_paths: ["/system/banner", "/a/b"] } : d));
    render(<Undesired devices={{ ...devices, data: extra }} now={NOW} />);

    const leaf01 = screen.getByRole("group", { name: "leaf-01" });
    expect(within(leaf01).getByText("/interface[name=ethernet-1/7]/description")).toBeInTheDocument();
    expect(within(screen.getByRole("group", { name: "leaf-03" })).getAllByRole("listitem")).toHaveLength(2);
    expect(screen.queryByRole("group", { name: "spine-01" })).not.toBeInTheDocument();
  });

  it("shows each device's remove command to copy, and nothing that would run it (spec 013 FR-016)", () => {
    render(<Undesired devices={devices} now={NOW} />);

    const leaf01 = screen.getByRole("group", { name: "leaf-01" });
    expect(within(leaf01).getByText("snapl remove leaf-01")).toBeInTheDocument();
    expect(within(leaf01).getByRole("button", { name: "Copy command: snapl remove leaf-01" })).toBeInTheDocument();
    expect(screen.getAllByRole("button").every((b) => b.getAttribute("aria-label")?.startsWith("Copy command:"))).toBe(true);
    expect(document.querySelector("form")).toBeNull();
  });

  it("says removal is never automatic", () => {
    render(<Undesired devices={devices} now={NOW} />);

    expect(screen.getByText(/never removed on a schedule/)).toBeInTheDocument();
    expect(screen.queryByText(/#65/)).not.toBeInTheDocument();
  });

  it("says when no device has any", () => {
    const clean = devices.data!.map((d) => ({ ...d, undesired_paths: [] }));
    render(<Undesired devices={{ ...devices, data: clean }} now={NOW} />);

    expect(screen.getByText("No undesired config on any device.")).toBeInTheDocument();
  });
});
