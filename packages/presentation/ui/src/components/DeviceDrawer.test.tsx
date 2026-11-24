import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { DeviceNetwork, DeviceView, FabricNetwork, View } from "../api/types";
import { DEVICES, DEVICES_AT_SCAN_2, NETWORK, NOW, OVERVIEW } from "../test/fixtures";
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

  describe("health (spec 016)", () => {
    const withHealth = (health: DeviceView["health"]) => ({ ...device("spine-01"), health });

    it("comes first: its state, since when, and each reason", () => {
      open({ device: device("spine-01") });

      const drawer = screen.getByRole("dialog", { name: "spine-01" });
      const health = within(drawer).getByRole("region", { name: "Health" });
      expect(within(drawer).getAllByRole("region")[0]).toBe(health);
      expect(within(health).getByText("Unhealthy")).toBeInTheDocument();
      // Recorded an hour before the page's now.
      expect(within(health).getByText("since 1 h ago")).toBeInTheDocument();
      expect(within(health).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
        "BGP 10.10.1.1 active (expected established)",
        "BGP 10.10.1.3 active (expected established)",
        "BGP 10.10.1.5 active (expected established)",
        "BGP 10.10.1.7 active (expected established)",
      ]);
    });

    it("says why the health is unknown when the worker is not running", () => {
      open({
        device: withHealth({
          state: "unknown",
          since: NOW.toISOString(),
          reasons: [{ kind: "no_telemetry", text: "the worker is not running" }],
        }),
      });

      const health = screen.getByRole("region", { name: "Health" });
      expect(within(health).getByText("Health unknown")).toBeInTheDocument();
      expect(within(health).getByText("the worker is not running")).toBeInTheDocument();
    });

    it("lists no reason for a healthy device", () => {
      open({ device: device("leaf-02") });

      const health = screen.getByRole("region", { name: "Health" });
      expect(within(health).getByText("Healthy")).toBeInTheDocument();
      expect(within(health).queryByRole("list")).not.toBeInTheDocument();
    });

    it("shows nothing when no health is recorded", () => {
      open({ device: withHealth(null) });

      expect(screen.queryByRole("region", { name: "Health" })).not.toBeInTheDocument();
    });

    it("leaves the drift state as it is", () => {
      open({ device: device("spine-01") });

      expect(screen.getByText("In sync")).toBeInTheDocument();
    });
  });

  it("lists undesired paths apart, with the remove command to copy and the note", () => {
    open();

    const undesired = screen.getByRole("region", { name: "On the device, not in intent" });
    expect(within(undesired).getByText("/interface[name=ethernet-1/7]/description")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "In intent, missing from the device" })).not.toBeInTheDocument();
    expect(screen.getByText(/Reconcile never removes config intent doesn't hold/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy command: snapl remove leaf-01" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Copy command: snapl reconcile/ })).not.toBeInTheDocument();
  });

  describe("removal (spec 013 FR-016)", () => {
    const both: DeviceView = {
      ...device("leaf-01"),
      drift_state: "missing_and_undesired",
      missing_paths: ["/interface[name=ethernet-1/1]/enabled"],
      suggested_command: "snapl reconcile leaf-01",
      remove_command: "snapl remove leaf-01",
    };
    const previewed: DeviceView = {
      ...device("leaf-01"),
      last_removal_preview: {
        at: new Date(NOW.getTime() - 5 * 60_000).toISOString(),
        workflow_id: "plan-removal-x",
        plan_paths: ["/interface[name=ethernet-1/7]", "/network-instance[name=default]/interface[name=ethernet-1/7.0]"],
        refused_paths: ["/routing-policy/policy[name=c-probe]"],
      },
    };

    it("shows the remove command beside reconcile when intent is also missing", () => {
      open({ device: both });

      expect(screen.getByText("snapl reconcile leaf-01")).toBeInTheDocument();
      expect(screen.getByText("snapl remove leaf-01")).toBeInTheDocument();
    });

    it("shows the latest preview: when, how many deletes, and the entities it refused", () => {
      open({ device: previewed });

      expect(screen.getByText("Last removal preview")).toBeInTheDocument();
      expect(screen.getByText("2 deletes, 1 refused · 5 min ago")).toBeInTheDocument();
      const refused = screen.getByRole("region", { name: "Left in place by the last preview" });
      expect(within(refused).getByText("/routing-policy/policy[name=c-probe]")).toBeInTheDocument();
    });

    it("says when the device was never previewed", () => {
      open();

      expect(screen.getByText("Last removal preview")).toBeInTheDocument();
      expect(screen.getByText("None recorded")).toBeInTheDocument();
    });

    it("offers no way to start a preview or a removal: only copy controls and close", () => {
      open({ device: { ...both, ...previewed, drift_state: "missing_and_undesired" } });

      const names = screen.getAllByRole("button").map((b) => b.getAttribute("aria-label") ?? b.textContent ?? "");
      expect(names.filter((name) => name !== "Close" && !name.startsWith("Copy command:"))).toEqual([]);
      expect(document.querySelector("form")).toBeNull();
    });
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

  it("lets each run be chosen to show its loop, and marks the one shown", async () => {
    const onSelectRun = vi.fn();
    open({
      device: device("spine-01"),
      onSelectRun,
      selectedRun: { workflow_id: "deploy-intent-18e29d6d-e0c5-0ba7-32d1-c510fc3ef077", started_at: "2026-11-07T21:18:56.168004Z" },
    });
    const list = screen.getByRole("list", { name: "Recent runs for spine-01" });
    const buttons = within(list).getAllByRole("button", { name: /^Show the loop for DeployIntent started/ });

    expect(buttons).toHaveLength(2);
    expect(buttons[0]).toHaveAccessibleName("Show the loop for DeployIntent started 2026-11-07 21:20:15 UTC");
    expect(buttons[0]).toHaveAttribute("aria-pressed", "false");
    expect(buttons[1]).toHaveAttribute("aria-pressed", "true");

    await userEvent.click(buttons[0]!);

    expect(onSelectRun).toHaveBeenCalledWith({
      workflow_id: "deploy-intent-18e29d6d-e0c5-0ba7-32d1-c510fc3ef077",
      started_at: "2026-11-07T21:20:15.648265Z",
    });
  });

  it("offers no loop where nothing can show one", () => {
    open({ device: device("spine-01") });

    expect(screen.queryByRole("button", { name: /^Show the loop/ })).not.toBeInTheDocument();
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

  // Spec 016 T045: the device's entry in /network, after what snapl itself found.
  describe("the network (spec 016)", () => {
    const network = NETWORK.network;
    const entry = (name: string) => network.data!.devices.find((d) => d.name === name)!;
    /** /network with one device's entry changed. */
    const changed = (name: string, change: Partial<DeviceNetwork>): View<FabricNetwork> => ({
      ...network,
      data: { ...network.data!, devices: network.data!.devices.map((d) => (d.name === name ? { ...d, ...change } : d)) },
    });
    const cells = (region: HTMLElement) =>
      within(region)
        .getAllByRole("row")
        .slice(1)
        .map((row) => within(row).getAllByRole("cell").map((cell) => cell.textContent));

    it("lists each interface in intent: up or down, and its traffic in and out", () => {
      open({ device: device("spine-01"), network });

      const interfaces = screen.getByRole("region", { name: "Interfaces" });
      expect(cells(interfaces)).toEqual([
        ["ethernet-1/1", "up", "1 kB/s", "1 kB/s"],
        ["ethernet-1/2", "up", "1 kB/s", "1 kB/s"],
        ["ethernet-1/3", "up", "1 kB/s", "1 kB/s"],
        ["ethernet-1/4", "up", "1 kB/s", "1.1 kB/s"],
        // A loopback has no counters: no data, never 0.
        ["lo0", "up", "no data", "no data"],
      ]);
      expect(within(interfaces).queryByRole("columnheader", { name: "Errors and discards" })).not.toBeInTheDocument();
    });

    it("flags an interface that is not as intent expects", () => {
      const interfaces = entry("spine-01").interfaces!.map((i) =>
        i.name === "ethernet-1/2" ? { ...i, up: false } : i.name === "ethernet-1/3" ? { ...i, up: null } : i,
      );
      open({ device: device("spine-01"), network: changed("spine-01", { interfaces }) });

      const region = screen.getByRole("region", { name: "Interfaces" });
      const row = within(region).getByRole("row", { name: /ethernet-1\/2/ });
      expect(within(row).getAllByRole("cell")[1]).toHaveTextContent(/^!down, expected up$/);
      expect(within(row).getByText("!")).toHaveAttribute("aria-hidden", "true");
      // The expectation is read out, and on the flag's title: shown, it wrapped the column (look check).
      expect(within(row).getByText("!")).toHaveAttribute("title", "Expected up");
      expect(within(row).getByText(", expected up")).toHaveClass("sr-only");
      expect(within(within(region).getByRole("row", { name: /ethernet-1\/3/ })).getAllByRole("cell")[1]).toHaveTextContent(/^no data$/);
    });

    // Under the interface's name, not in a fifth column: a fifth wrapped every cell (look check).
    it("names errors and discards only on the interfaces that have them, under the name", () => {
      open({ device: device("leaf-02"), network });

      const region = screen.getByRole("region", { name: "Interfaces" });
      expect(within(region).getAllByRole("columnheader")).toHaveLength(4);
      expect(cells(region).map((row) => row[0])).toEqual(["ethernet-1/49errors in 12/min", "ethernet-1/50", "lo0"]);
      expect(within(region).getByText("errors in 12/min")).toHaveClass("text-meta");
    });

    it("lists each BGP session: established or not against intent, and the prefixes received", () => {
      open({ device: device("spine-01"), network });

      const sessions = screen.getByRole("region", { name: "BGP sessions" });
      expect(cells(sessions)[0]).toEqual(["10.10.1.1", "!not established, expected established", "0"]);
      expect(cells(sessions)).toHaveLength(4);
    });

    it("shows an established session plainly", () => {
      open({ device: device("leaf-01"), network });

      expect(cells(screen.getByRole("region", { name: "BGP sessions" }))[0]).toEqual(["10.10.1.0", "established", "2"]);
    });

    it("shows a missing value as no data, never 0", () => {
      const sessions = [{ peer: "10.10.1.1", expected: null, established: null, prefixes_received: null }];
      open({ device: device("spine-01"), network: changed("spine-01", { sessions, cpu_percent: null }) });

      expect(cells(screen.getByRole("region", { name: "BGP sessions" }))).toEqual([["10.10.1.1", "no data", "no data"]]);
      expect(screen.getByRole("region", { name: "CPU and memory" })).toHaveTextContent("CPUno dataMemory94%");
    });

    it("gives CPU and memory in percent, with the lab note", () => {
      open({ device: device("spine-01"), network });

      const region = screen.getByRole("region", { name: "CPU and memory" });
      expect(region).toHaveTextContent("CPU3%Memory94%");
      expect(region).toHaveTextContent("On the lab, this is the host VM's, the same on every device");
    });

    it("shows the reason when the network cannot be read", () => {
      const down: View<FabricNetwork> = {
        source: "prometheus",
        available: false,
        reason: "Prometheus is unreachable",
        remedy: "uv run invoke dev.deps",
        data: null,
      };
      open({ device: device("spine-01"), network: down });

      const region = screen.getByRole("region", { name: "Network" });
      expect(region).toHaveTextContent("Prometheus is unreachable");
      expect(region).toHaveTextContent("uv run invoke dev.deps");
      expect(screen.queryByRole("region", { name: "Interfaces" })).not.toBeInTheDocument();
    });

    it("comes after what snapl found, and shows nothing without an answer or an entry", () => {
      const { unmount } = open({ device: device("spine-01"), network });
      const drawer = screen.getByRole("dialog", { name: "spine-01" });
      const regions = within(drawer).getAllByRole("region").map((r) => r.getAttribute("aria-label"));
      expect(regions).toEqual(["Health", "Interfaces", "BGP sessions", "CPU and memory"]);
      expect(
        within(drawer).getByText("Recent runs").compareDocumentPosition(within(drawer).getByRole("region", { name: "Interfaces" })) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
      unmount();

      open({ device: device("spine-01") });
      expect(screen.queryByRole("region", { name: "Interfaces" })).not.toBeInTheDocument();
      expect(screen.queryByRole("region", { name: "Network" })).not.toBeInTheDocument();
    });

    it("shows nothing for a device /network does not list", () => {
      open({ device: device("spine-01"), network: { ...network, data: { ...network.data!, devices: [] } } });

      expect(screen.queryByRole("region", { name: "Interfaces" })).not.toBeInTheDocument();
    });
  });
});
