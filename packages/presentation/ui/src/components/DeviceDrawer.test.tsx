import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { DEVICES, DEVICES_AT_SCAN_2, NOW, OVERVIEW } from "../test/fixtures";
import { DeviceDrawer } from "./DeviceDrawer";

const device = (name: string, from = DEVICES) => from.devices.data!.find((d) => d.name === name)!;
const runs = OVERVIEW.runs.data!;

function open(overrides: Partial<Parameters<typeof DeviceDrawer>[0]> = {}) {
  const onClose = vi.fn();
  const view = render(
    <DeviceDrawer device={device("leaf-01")} runs={runs} linkNotes={[]} now={NOW} onClose={onClose} {...overrides} />,
  );
  return { ...view, onClose };
}

describe("DeviceDrawer", () => {
  it("names the device, its role and its state in words", () => {
    open();

    const drawer = screen.getByRole("dialog", { name: "leaf-01" });
    expect(within(drawer).getByText("leaf")).toBeInTheDocument();
    expect(within(drawer).getByText("Undesired config")).toBeInTheDocument();
  });

  it("lists undesired paths apart, says removal is not automatic, and offers no command", () => {
    open();

    const undesired = screen.getByRole("region", { name: "On the device, not in intent" });
    expect(within(undesired).getByText("/interface[name=ethernet-1/7]/description")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "In intent, missing from the device" })).not.toBeInTheDocument();
    expect(screen.getByText("Config on the device but not in intent is not removed automatically (#65).")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Copy command/ })).not.toBeInTheDocument();
  });

  it("lists missing paths and shows the reconcile command with a copy control", () => {
    open({ device: device("spine-01", DEVICES_AT_SCAN_2) });

    const missing = screen.getByRole("region", { name: "In intent, missing from the device" });
    expect(within(missing).getByText("/interface[name=ethernet-1/1]/enabled")).toBeInTheDocument();
    expect(screen.getByText("snapl reconcile spine-01")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy command: snapl reconcile spine-01" })).toBeInTheDocument();
  });

  it("shows no paths for a device in sync", () => {
    open({ device: device("leaf-02") });

    expect(screen.queryByRole("region", { name: /intent/ })).not.toBeInTheDocument();
    expect(screen.getByText("In sync")).toBeInTheDocument();
  });

  it("shows when it was last scanned and how its last deploy ended", () => {
    open();

    expect(screen.getByText("Last scan")).toBeInTheDocument();
    expect(screen.getByText("Last deploy")).toBeInTheDocument();
    expect(screen.getByText(/^Succeeded · /)).toBeInTheDocument();
    // Ages are relative to the page's now, 30 s after the arc's last scan.
    expect(screen.getByText(/30 s ago/)).toBeInTheDocument();
  });

  it("says so when a device has never been scanned or deployed", () => {
    const fresh = { ...device("leaf-02"), drift_state: "never_scanned", last_scanned_at: null, last_scan_workflow_id: null, last_deploy: null };

    open({ device: fresh as ReturnType<typeof device> });

    expect(screen.getByText("Never scanned")).toBeInTheDocument();
    expect(screen.getByText("never")).toBeInTheDocument();
    expect(screen.getByText("No deploy recorded")).toBeInTheDocument();
  });

  it("shows a failed deploy's reason", () => {
    const failed = {
      ...device("leaf-02"),
      last_deploy: { workflow_id: "deploy-intent-x", success: false, reason: "apply_failed", ended_at: "2026-11-07T21:20:00Z" },
    };

    open({ device: failed as ReturnType<typeof device> });

    expect(screen.getByText(/Failed: apply_failed/)).toBeInTheDocument();
  });

  it("lists this device's recent runs only, each with its outcome in words", () => {
    open({ device: device("spine-01") });

    const list = screen.getByRole("list", { name: "Recent runs for spine-01" });
    const items = within(list).getAllByRole("listitem");
    // spine-01 was deployed twice in the arc: by the operator, then by the reconcile.
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("DeployIntent");
    expect(items[0]).toHaveTextContent("Succeeded");
  });

  it("says so when the audit trail holds no run for the device", () => {
    open({ runs: [] });

    expect(screen.getByText("No runs recorded for this device.")).toBeInTheDocument();
  });

  it("renders text from the device as text, never as markup", () => {
    const hostile = {
      ...device("leaf-02"),
      drift_state: "error",
      error: "<img src=x onerror=alert(1)>",
      undesired_paths: [],
    };

    open({ device: hostile as ReturnType<typeof device> });

    expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });

  it("shows a path that looks like markup as text", () => {
    const hostile = { ...device("leaf-01"), undesired_paths: ['/interface[name=<script>alert("x")</script>]'] };

    open({ device: hostile });

    expect(screen.getByText('/interface[name=<script>alert("x")</script>]')).toBeInTheDocument();
    expect(document.querySelector("script")).toBeNull();
  });

  it("says when the two ends of one of its links do not agree", () => {
    open({ linkNotes: ["ethernet-1/49 names spine-01 ethernet-1/1, which does not name it back."] });

    expect(screen.getByText("ethernet-1/49 names spine-01 ethernet-1/1, which does not name it back.")).toBeInTheDocument();
  });

  it("says a device is no longer in intent", () => {
    open({ device: { ...device("leaf-02"), in_intent: false } });

    expect(screen.getByText("No longer in intent.")).toBeInTheDocument();
  });

  it("closes with its button and with Escape", async () => {
    const { onClose } = open();

    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    await userEvent.keyboard("{Escape}");

    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("takes the focus when it opens and gives it back when it closes", () => {
    const opener = document.createElement("button");
    document.body.append(opener);
    opener.focus();

    const { unmount } = open();
    expect(screen.getByRole("button", { name: "Close" })).toHaveFocus();

    unmount();
    expect(opener).toHaveFocus();
    opener.remove();
  });

  it("offers no copy control in a recording", () => {
    open({ device: device("spine-01", DEVICES_AT_SCAN_2), copy: false });

    expect(screen.getByText("snapl reconcile spine-01")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Copy command/ })).not.toBeInTheDocument();
  });
});
