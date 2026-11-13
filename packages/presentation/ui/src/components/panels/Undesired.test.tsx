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

  it("says removal is not automatic, and offers no command", () => {
    render(<Undesired devices={devices} now={NOW} />);

    expect(screen.getByText(/not removed automatically \(#65\)/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Copy/ })).not.toBeInTheDocument();
  });

  it("says when no device has any", () => {
    const clean = devices.data!.map((d) => ({ ...d, undesired_paths: [] }));
    render(<Undesired devices={{ ...devices, data: clean }} now={NOW} />);

    expect(screen.getByText("No undesired config on any device.")).toBeInTheDocument();
  });
});
