// Where the topology's devices and links are drawn (spec 011 research R7).
// A pure function of intent: the same devices and links give the same drawing
// on every load, in any arrival order.

export interface LayoutNode {
  name: string;
  role: string;
}

export interface LayoutLink {
  a: { device: string; interface: string };
  b: { device: string; interface: string };
}

export interface Size {
  width: number;
  height: number;
}

export interface PlacedNode<N extends LayoutNode> {
  node: N;
  name: string;
  role: string;
  x: number;
  y: number;
}

export interface PlacedLink<L extends LayoutLink> {
  link: L;
  key: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

export type Layout<N extends LayoutNode, L extends LayoutLink> =
  | { kind: "placed"; nodes: PlacedNode<N>[]; links: PlacedLink<L>[]; tiers: { role: string; y: number }[] }
  | { kind: "too-many"; count: number; limit: number };

/** More devices than this do not fit one screen without pan and zoom (spec Assumptions). */
export const MAX_DEVICES = 32;

const KNOWN_TIERS = ["spine", "leaf"];
const natural = new Intl.Collator("en", { numeric: true, sensitivity: "base" });

function tierOrder(a: string, b: string): number {
  const [ia, ib] = [KNOWN_TIERS.indexOf(a), KNOWN_TIERS.indexOf(b)];
  if (ia !== -1 || ib !== -1) return (ia === -1 ? KNOWN_TIERS.length : ia) - (ib === -1 ? KNOWN_TIERS.length : ib);
  return natural.compare(a, b);
}

/** A link's identity: both its ends. */
export function linkKey(link: LayoutLink): string {
  return `${link.a.device}:${link.a.interface}|${link.b.device}:${link.b.interface}`;
}

/** Devices in tiers by role, top to bottom, each tier spread evenly left to right. */
export function layoutTiers<N extends LayoutNode, L extends LayoutLink>(
  nodes: readonly N[],
  links: readonly L[],
  size: Size,
  limit: number = MAX_DEVICES,
): Layout<N, L> {
  if (nodes.length > limit) return { kind: "too-many", count: nodes.length, limit };

  const roles = [...new Set(nodes.map((node) => node.role))].sort(tierOrder);
  const tiers = roles.map((role, index) => ({ role, y: (size.height * (index + 1)) / (roles.length + 1) }));
  const placed = tiers.flatMap((tier) => {
    const inTier = nodes.filter((node) => node.role === tier.role).sort((a, b) => natural.compare(a.name, b.name));
    return inTier.map((node, index) => ({
      node,
      name: node.name,
      role: node.role,
      x: (size.width * (index + 1)) / (inTier.length + 1),
      y: tier.y,
    }));
  });

  const at = new Map(placed.map((node) => [node.name, node]));
  const drawn = links
    .flatMap((link) => {
      const [a, b] = [at.get(link.a.device), at.get(link.b.device)];
      return a && b ? [{ link, key: linkKey(link), x1: a.x, y1: a.y, x2: b.x, y2: b.y }] : [];
    })
    .sort((a, b) => natural.compare(a.key, b.key));

  return { kind: "placed", nodes: placed, links: drawn, tiers };
}
