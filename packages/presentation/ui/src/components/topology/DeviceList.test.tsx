import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { DEVICES } from "../../test/fixtures";
import { DeviceList } from "./DeviceList";

const devices = DEVICES.devices.data!;

describe("DeviceList", () => {
  it("lists each device with its role and its state in words", () => {
    render(<DeviceList devices={devices} selected={undefined} onSelect={() => {}} />);

    const rows = screen.getAllByRole("button");
    expect(rows).toHaveLength(6);
    expect(rows[0]).toHaveAccessibleName("spine-01, spine, In sync");
    expect(rows[2]).toHaveAccessibleName("leaf-01, leaf, Undesired config");
  });

  it("says a role is unknown when the Source of Truth could not be read", () => {
    const degraded = devices.map((device) => ({ ...device, role: null }));

    render(<DeviceList devices={degraded} selected={undefined} onSelect={() => {}} />);

    expect(screen.getAllByRole("button")[0]).toHaveAccessibleName("spine-01, role unknown, In sync");
  });

  it("selects a device and says which one is selected", async () => {
    const onSelect = vi.fn();
    render(<DeviceList devices={devices} selected="leaf-01" onSelect={onSelect} />);

    await userEvent.click(screen.getByRole("button", { name: /^leaf-03,/ }));

    expect(onSelect).toHaveBeenCalledWith("leaf-03");
    expect(screen.getByRole("button", { name: /^leaf-01,/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("says so when there are no devices", () => {
    render(<DeviceList devices={[]} selected={undefined} onSelect={() => {}} />);

    expect(screen.getByText("No devices recorded yet.")).toBeInTheDocument();
  });
});
