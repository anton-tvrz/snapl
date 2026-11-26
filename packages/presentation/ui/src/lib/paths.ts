import { type LayoutLink, linkKey } from "./layout";

const LEADING_INTERFACE = /^\/interface\[name=([^\]]+)\]/;

/**
 * The interface a drift path is about, or null. Paths are snapl's own
 * normalised ones, keyed gNMI-style: `/interface[name=ethernet-1/1]/enabled`.
 */
export function interfaceOf(path: string): string | null {
  return LEADING_INTERFACE.exec(path)?.[1] ?? null;
}

export interface DevicePaths {
  name: string;
  missing_paths?: string[];
  undesired_paths?: string[];
  unclassified_paths?: string[];
}

/**
 * The links that carry drift: those with an end on an interface that one of
 * that end's device's differing paths names (spec 011 FR-014). A path that
 * names no interface, or an interface with no link, marks the device only.
 */
export function markedLinks(devices: readonly DevicePaths[], links: readonly LayoutLink[]): Set<string> {
  const drifted = new Set<string>();
  for (const device of devices) {
    const paths = [
      ...(device.missing_paths ?? []),
      ...(device.undesired_paths ?? []),
      ...(device.unclassified_paths ?? []),
    ];
    for (const path of paths) {
      const name = interfaceOf(path);
      if (name !== null) drifted.add(`${device.name}:${name}`);
    }
  }
  const ends = (link: LayoutLink) => [`${link.a.device}:${link.a.interface}`, `${link.b.device}:${link.b.interface}`];
  return new Set(links.filter((link) => ends(link).some((end) => drifted.has(end))).map(linkKey));
}
