import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { linkKey } from "../../lib/layout";
import { markedLinks } from "../../lib/paths";
import { DEVICES, DEVICES_AT_SCAN_2, TOPOLOGY } from "../../test/fixtures";
import { Topology } from "./Topology";

const topology = TOPOLOGY.topology.data!;
const devices = DEVICES.devices.data!;
const devicesAtScan2 = DEVICES_AT_SCAN_2.devices.data!;

afterEach(() => {
  vi.unstubAllGlobals();
});

function draw(overrides: Partial<Parameters<typeof Topology>[0]> = {}) {
  const onSelect = vi.fn();
  const view = render(
    <Topology
      topology={topology}
      devices={devices}
      activity={{ devices: new Map(), fabric: null }}
      marked={markedLinks(devices, topology.links)}
      flagged={new Map()}
      selected={undefined}
      onSelect={onSelect}
      {...overrides}
    />,
  );
  return { ...view, onSelect };
}

const links = (container: HTMLElement) => [...container.querySelectorAll<SVGElement>("[data-link]")];

describe("Topology", () => {
  it("draws the six devices as buttons, each named with its role and state in words", () => {
    draw();

    const buttons = screen.getAllByRole("button");
    expect(buttons.map((button) => button.getAttribute("aria-label"))).toEqual([
      "spine-01, spine, In sync, Unhealthy",
      "spine-02, spine, In sync, Unhealthy",
      "leaf-01, leaf, Undesired config, Healthy",
      "leaf-02, leaf, In sync, Healthy",
      "leaf-03, leaf, In sync, Healthy",
      "leaf-04, leaf, In sync, Healthy",
    ]);
  });

  describe("health (spec 016)", () => {
    const withHealth = (name: string, health: (typeof devices)[number]["health"]) =>
      devices.map((device) => (device.name === name ? { ...device, health } : device));
    const chip = (name: string) => screen.getByRole("button", { name: new RegExp(`^${name},`) });
    const mark = (name: string) => chip(name).querySelector<SVGElement>("[data-health]");

    it("marks an unhealthy device with a red ! beside its drift colour", () => {
      draw();

      expect(mark("spine-01")?.dataset.health).toBe("unhealthy");
      expect(mark("spine-01")?.textContent).toBe("!");
      expect(mark("spine-01")?.querySelector("circle")?.getAttribute("fill")).toBe("#d03b3b");
    });

    it("draws no mark on a healthy device, nor on one with no recorded health", () => {
      draw({ devices: withHealth("leaf-02", null) });

      expect(mark("leaf-01")).toBeNull();
      expect(mark("leaf-02")).toBeNull();
      expect(chip("leaf-02")).toHaveAccessibleName("leaf-02, leaf, In sync");
    });

    it("marks a device whose health is unknown with a grey ?", () => {
      draw({ devices: withHealth("leaf-03", { state: "unknown", since: "2026-11-07T21:00:00Z", reasons: [] }) });

      expect(mark("leaf-03")?.dataset.health).toBe("unknown");
      expect(mark("leaf-03")?.textContent).toBe("?");
      expect(chip("leaf-03")).toHaveAccessibleName("leaf-03, leaf, In sync, Health unknown");
    });

    it("leaves the drift state as it is: an unhealthy device in sync is drawn in sync", () => {
      draw();

      expect(chip("spine-01").dataset.shape).toBe("circle");
      expect(chip("spine-02").dataset.shape).toBe(chip("leaf-02").dataset.shape);
    });

    it("names the reasons in the chip's title", () => {
      draw();

      expect(chip("spine-01").querySelector("title")?.textContent).toBe(
        "Unhealthy: BGP 10.10.1.1 active (expected established); BGP 10.10.1.3 active (expected established); " +
          "BGP 10.10.1.5 active (expected established); BGP 10.10.1.7 active (expected established)",
      );
      expect(chip("leaf-02").querySelector("title")).toBeNull();
    });

    it("explains the health marks shown, in the legend's row", () => {
      const key = () => screen.queryByRole("list", { name: "Health marks shown" });
      draw({ devices: withHealth("leaf-03", { state: "unknown", since: "2026-11-07T21:00:00Z", reasons: [] }) });
      expect(within(key()!).getAllByRole("listitem").map((item) => item.textContent)).toEqual(["!Unhealthy", "?Health unknown"]);

      cleanup();
      draw();
      expect(within(key()!).getAllByRole("listitem").map((item) => item.textContent)).toEqual(["!Unhealthy"]);

      cleanup();
      draw({ devices: devices.map((device) => ({ ...device, health: null })) });
      expect(key()).not.toBeInTheDocument();
    });
  });

  it("puts the top tier's names above their marks, where no link crosses them", () => {
    draw();
    const nameY = (name: string) =>
      Number(screen.getByRole("button", { name: new RegExp(`^${name},`) }).querySelector("text.font-mono")?.getAttribute("y"));

    expect(nameY("spine-01")).toBeLessThan(0);
    expect(nameY("leaf-01")).toBeGreaterThan(0);
  });

  it("draws spines above leaves", () => {
    draw();
    const y = (name: string) => Number(screen.getByRole("button", { name: new RegExp(`^${name},`) }).dataset.y);

    expect(y("spine-01")).toBe(y("spine-02"));
    expect(y("spine-01")).toBeLessThan(y("leaf-01"));
  });

  it("draws the eight links, each described by its two ends", () => {
    const { container } = draw();

    expect(links(container)).toHaveLength(8);
    expect(within(container).getByText("leaf-01 ethernet-1/49 to spine-01 ethernet-1/1")).toBeInTheDocument();
  });

  it("gives each state its own shape, not just its own colour", () => {
    draw();

    expect(screen.getByRole("button", { name: /^leaf-01,/ }).dataset.shape).toBe("square");
    expect(screen.getByRole("button", { name: /^leaf-02,/ }).dataset.shape).toBe("circle");
  });

  it("marks the link on the interface a drifted path names", () => {
    // At the arc's second scan spine-01's ethernet-1/1, which faces leaf-01, was still disabled.
    const { container } = draw({ devices: devicesAtScan2, marked: markedLinks(devicesAtScan2, topology.links) });

    const marked = links(container).filter((link) => link.dataset.marked === "true");
    expect(marked.map((link) => link.dataset.link)).toEqual([linkKey(topology.links[0]!)]);
    expect(within(container).getByText("leaf-01 ethernet-1/49 to spine-01 ethernet-1/1: carries drift")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^spine-01,/ })).toHaveAccessibleName("spine-01, spine, Missing intent, Unhealthy");
  });

  it("marks no link when no drifted path names a linked interface", () => {
    const { container } = draw();

    // leaf-01's undesired path is on ethernet-1/7, which has no link.
    expect(links(container).filter((link) => link.dataset.marked === "true")).toEqual([]);
  });

  it("lists in the legend only the states that are shown", () => {
    draw();

    const legend = screen.getByRole("list", { name: "Drift states shown" });
    expect(within(legend).getAllByRole("listitem").map((item) => item.querySelector("[data-label]")?.textContent)).toEqual([
      "In sync",
      "Undesired config",
    ]);
  });

  it("selects a device with a click, and with Enter or Space from the keyboard", async () => {
    const { onSelect } = draw();

    await userEvent.click(screen.getByRole("button", { name: /^leaf-01,/ }));
    expect(onSelect).toHaveBeenLastCalledWith("leaf-01");

    screen.getByRole("button", { name: /^spine-02,/ }).focus();
    await userEvent.keyboard("{Enter}");
    expect(onSelect).toHaveBeenLastCalledWith("spine-02");
    await userEvent.keyboard(" ");
    expect(onSelect).toHaveBeenCalledTimes(3);
  });

  it("reaches the devices with Tab in tier order", async () => {
    draw();

    await userEvent.tab();
    expect(screen.getByRole("button", { name: /^spine-01,/ })).toHaveFocus();
    await userEvent.tab();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: /^leaf-01,/ })).toHaveFocus();
  });

  it("says which device is selected", () => {
    draw({ selected: "leaf-01" });

    expect(screen.getByRole("button", { name: /^leaf-01,/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /^leaf-02,/ })).toHaveAttribute("aria-pressed", "false");
  });

  it("shows work in progress and work that just finished, in words too", () => {
    draw({
      activity: {
        devices: new Map([
          ["leaf-02", { kind: "active", workflowType: "DeployIntent" }],
          ["leaf-03", { kind: "recent", workflowType: "DeployIntent", outcome: "failed" }],
        ]),
        fabric: null,
      },
    });

    expect(screen.getByRole("button", { name: /^leaf-02,/ })).toHaveAccessibleName(
      "leaf-02, leaf, In sync, Healthy, DeployIntent running",
    );
    expect(screen.getByRole("button", { name: /^leaf-03,/ })).toHaveAccessibleName(
      "leaf-03, leaf, In sync, Healthy, DeployIntent just ended: Failed",
    );
    expect(screen.getByRole("button", { name: /^leaf-02,/ }).querySelector("[data-activity='active']")).not.toBeNull();
  });

  it("pulses for activity, and shows a static ring when the viewer asks for reduced motion", () => {
    const active = { devices: new Map([["leaf-02", { kind: "active" as const, workflowType: "DeployIntent" }]]), fabric: null };

    const moving = draw({ activity: active });
    expect(moving.container.querySelector("[data-activity]")?.getAttribute("class")).toContain("activity-pulse");
    moving.unmount();

    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: query.includes("prefers-reduced-motion"),
      addEventListener: () => {},
      removeEventListener: () => {},
    }));
    const still = draw({ activity: active });
    expect(still.container.querySelector("[data-activity]")?.getAttribute("class")).not.toContain("activity-pulse");
    expect(still.container.querySelector("[data-activity]")).not.toBeNull();
  });

  it("says when a run is working on the whole fabric", () => {
    draw({ activity: { devices: new Map(), fabric: { kind: "active", workflowType: "ScanDrift" } } });

    expect(screen.getByRole("status")).toHaveTextContent("ScanDrift running on the fabric");
    expect(screen.getByRole("button", { name: /^leaf-02,/ }).querySelector("[data-activity]")).toBeNull();
  });

  it("marks a device whose scheduled repair keeps failing", () => {
    draw({ flagged: new Map([["leaf-04", 4]]) });

    expect(screen.getByRole("button", { name: /^leaf-04,/ })).toHaveAccessibleName(
      "leaf-04, leaf, In sync, Healthy, repair failed 4 times in a row",
    );
  });

  it("shows a device the device view has no state for as never scanned", () => {
    draw({ devices: [] });

    expect(screen.getByRole("button", { name: /^spine-01,/ })).toHaveAccessibleName("spine-01, spine, Never scanned");
  });

  it("shows a state a newer server sent by its raw text", () => {
    const odd = devices.map((device) => (device.name === "leaf-02" ? { ...device, drift_state: "quarantined" } : device));

    draw({ devices: odd as typeof devices });

    expect(screen.getByRole("button", { name: /^leaf-02,/ })).toHaveAccessibleName("leaf-02, leaf, quarantined, Healthy");
  });

  it("shows the device list in place of the drawing when there are too many devices", () => {
    const nodes = Array.from({ length: 33 }, (_, i) => ({ device_id: `id-${i}`, name: `leaf-${i}`, role: "leaf" }));

    const { container } = draw({ topology: { nodes, links: [], unlinked: [] }, devices: [] });

    expect(container.querySelector("svg[aria-label='Fabric topology']")).toBeNull();
    expect(screen.getByText("33 devices is more than the 32 this drawing fits on one screen. Showing the list.")).toBeInTheDocument();
    expect(screen.getAllByRole("button")).toHaveLength(33);
  });
});
