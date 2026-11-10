import { describe, expect, it } from "vitest";

import { layoutTiers, linkKey, MAX_DEVICES } from "./layout";

const SIZE = { width: 800, height: 400 };
const node = (name: string, role: string) => ({ device_id: name, name, role });
const link = (aDevice: string, aIf: string, bDevice: string, bIf: string) => ({
  a: { device: aDevice, interface: aIf },
  b: { device: bDevice, interface: bIf },
  declared_by: "both" as const,
  agreed: true,
});

const SPINES = ["spine-01", "spine-02"];
const LEAVES = ["leaf-01", "leaf-02", "leaf-03", "leaf-04"];
const DCFABRIC = {
  nodes: [...SPINES.map((name) => node(name, "spine")), ...LEAVES.map((name) => node(name, "leaf"))],
  links: LEAVES.flatMap((leaf) => [
    link(leaf, "ethernet-1/49", "spine-01", `ethernet-1/${LEAVES.indexOf(leaf) + 1}`),
    link(leaf, "ethernet-1/50", "spine-02", `ethernet-1/${LEAVES.indexOf(leaf) + 1}`),
  ]),
};

function placed(nodes = DCFABRIC.nodes, links = DCFABRIC.links) {
  const result = layoutTiers(nodes, links, SIZE);
  if (result.kind !== "placed") throw new Error("expected a placed layout");
  return result;
}

describe("layoutTiers", () => {
  it("draws dcfabric in two tiers, spines above leaves, in name order", () => {
    const layout = placed();

    expect(layout.tiers.map((tier) => tier.role)).toEqual(["spine", "leaf"]);
    const spines = layout.nodes.filter((n) => n.role === "spine");
    const leaves = layout.nodes.filter((n) => n.role === "leaf");
    expect(spines.map((n) => n.name)).toEqual(SPINES);
    expect(leaves.map((n) => n.name)).toEqual(LEAVES);
    expect(new Set(spines.map((n) => n.y)).size).toBe(1);
    expect(new Set(leaves.map((n) => n.y)).size).toBe(1);
    expect(spines[0]!.y).toBeLessThan(leaves[0]!.y);
  });

  it("spaces a tier evenly, left to right, inside the drawing", () => {
    const leaves = placed().nodes.filter((n) => n.role === "leaf");
    const xs = leaves.map((n) => n.x);

    expect(xs).toEqual([...xs].sort((a, b) => a - b));
    const gaps = xs.slice(1).map((x, i) => x - xs[i]!);
    for (const gap of gaps) expect(gap).toBeCloseTo(gaps[0]!, 6);
    for (const n of placed().nodes) {
      expect(n.x).toBeGreaterThan(0);
      expect(n.x).toBeLessThan(SIZE.width);
      expect(n.y).toBeGreaterThan(0);
      expect(n.y).toBeLessThan(SIZE.height);
    }
  });

  it("joins each link's two devices", () => {
    const layout = placed();
    const at = Object.fromEntries(layout.nodes.map((n) => [n.name, n]));

    expect(layout.links).toHaveLength(8);
    for (const drawn of layout.links) {
      expect([drawn.x1, drawn.y1]).toEqual([at[drawn.link.a.device]!.x, at[drawn.link.a.device]!.y]);
      expect([drawn.x2, drawn.y2]).toEqual([at[drawn.link.b.device]!.x, at[drawn.link.b.device]!.y]);
    }
    expect(new Set(layout.links.map((drawn) => drawn.key)).size).toBe(8);
  });

  it("gives the same drawing for the same intent, whatever order it arrives in", () => {
    const shuffled = layoutTiers([...DCFABRIC.nodes].reverse(), [...DCFABRIC.links].reverse(), SIZE);

    expect(shuffled).toEqual(layoutTiers(DCFABRIC.nodes, DCFABRIC.links, SIZE));
  });

  it("compares the numbers in names as numbers", () => {
    const layout = placed([node("leaf-10", "leaf"), node("leaf-2", "leaf"), node("leaf-1", "leaf")], []);

    expect(layout.nodes.map((n) => n.name)).toEqual(["leaf-1", "leaf-2", "leaf-10"]);
  });

  it("gives a role it does not know its own tier, after the known ones, by name", () => {
    const layout = placed(
      [node("wan-1", "wan"), node("edge-1", "border"), node("leaf-1", "leaf"), node("spine-1", "spine")],
      [],
    );

    expect(layout.tiers.map((tier) => tier.role)).toEqual(["spine", "leaf", "border", "wan"]);
    expect(new Set(layout.nodes.map((n) => `${n.x},${n.y}`)).size).toBe(4);
  });

  it("places one device, and devices with no links, without error", () => {
    expect(placed([node("edge-01", "leaf")], []).nodes).toHaveLength(1);
    expect(placed(DCFABRIC.nodes, []).links).toEqual([]);
  });

  it("leaves out a link whose device is not drawn", () => {
    const layout = placed([node("leaf-1", "leaf")], [link("leaf-1", "e1", "ghost", "e1")]);

    expect(layout.links).toEqual([]);
  });

  it("refuses more devices than fit one screen, with the count and the limit", () => {
    const many = Array.from({ length: MAX_DEVICES + 1 }, (_, i) => node(`leaf-${i}`, "leaf"));

    expect(MAX_DEVICES).toBe(32);
    expect(layoutTiers(many, [], SIZE)).toEqual({ kind: "too-many", count: 33, limit: 32 });
    expect(layoutTiers(many.slice(0, 32), [], SIZE).kind).toBe("placed");
  });

  it("is empty for no devices", () => {
    expect(placed([], [])).toMatchObject({ nodes: [], links: [], tiers: [] });
  });
});

describe("linkKey", () => {
  it("names a link by both its ends", () => {
    expect(linkKey(link("leaf-01", "ethernet-1/49", "spine-01", "ethernet-1/1"))).toBe(
      "leaf-01:ethernet-1/49|spine-01:ethernet-1/1",
    );
  });
});
