import { describe, expect, it } from "vitest";

import { linkKey } from "./layout";
import { interfaceOf, markedLinks } from "./paths";

const link = (aDevice: string, aIf: string, bDevice: string, bIf: string) => ({
  a: { device: aDevice, interface: aIf },
  b: { device: bDevice, interface: bIf },
  declared_by: "both" as const,
  agreed: true,
});
const device = (name: string, paths: Partial<Record<"missing_paths" | "undesired_paths" | "unclassified_paths", string[]>>) => ({
  name,
  missing_paths: [],
  undesired_paths: [],
  unclassified_paths: [],
  ...paths,
});

describe("interfaceOf", () => {
  it("reads the interface a path starts with", () => {
    // The two paths the captured demo arc recorded.
    expect(interfaceOf("/interface[name=ethernet-1/1]/enabled")).toBe("ethernet-1/1");
    expect(interfaceOf("/interface[name=ethernet-1/7]/description")).toBe("ethernet-1/7");
    expect(interfaceOf("/interface[name=lag1.0]/subinterface[index=0]/admin-state")).toBe("lag1.0");
    expect(interfaceOf("/interface[name=lo0]")).toBe("lo0");
  });

  it("is null for a path that names no interface", () => {
    expect(interfaceOf("/network-instance[name=default]/protocols/bgp/neighbor[peer-address=10.0.0.1]")).toBeNull();
    expect(interfaceOf("/system/name")).toBeNull();
    expect(interfaceOf("")).toBeNull();
  });

  it("only reads a leading interface segment", () => {
    expect(interfaceOf("/network-instance[name=default]/interface[name=ethernet-1/1.0]")).toBeNull();
  });
});

describe("markedLinks", () => {
  const SPINE_LEAF = link("leaf-01", "ethernet-1/49", "spine-01", "ethernet-1/1");
  const OTHER = link("leaf-02", "ethernet-1/49", "spine-01", "ethernet-1/2");

  it("marks the link on the interface a drifted path names (research R8)", () => {
    const devices = [
      device("spine-01", { missing_paths: ["/interface[name=ethernet-1/1]/enabled"] }),
      device("leaf-01", { undesired_paths: ["/interface[name=ethernet-1/7]/description"] }),
    ];

    const marked = markedLinks(devices, [SPINE_LEAF, OTHER]);

    // spine-01's ethernet-1/1 faces leaf-01. leaf-01's ethernet-1/7 has no link.
    expect([...marked]).toEqual([linkKey(SPINE_LEAF)]);
  });

  it("marks from either end, and from undesired and unclassified paths alike", () => {
    expect(markedLinks([device("leaf-01", { undesired_paths: ["/interface[name=ethernet-1/49]/mtu"] })], [SPINE_LEAF]).size).toBe(1);
    expect(markedLinks([device("leaf-01", { unclassified_paths: ["/interface[name=ethernet-1/49]/mtu"] })], [SPINE_LEAF]).size).toBe(1);
  });

  it("does not mark a link for the same interface name on another device", () => {
    const devices = [device("spine-02", { missing_paths: ["/interface[name=ethernet-1/1]/enabled"] })];

    expect(markedLinks(devices, [SPINE_LEAF]).size).toBe(0);
  });

  it("marks nothing for a path that names no interface", () => {
    expect(markedLinks([device("spine-01", { missing_paths: ["/system/name"] })], [SPINE_LEAF]).size).toBe(0);
    expect(markedLinks([], [SPINE_LEAF]).size).toBe(0);
  });
});
