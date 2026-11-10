import { describe, expect, it } from "vitest";

import { linkNotes } from "./linkNotes";

const end = (device: string, iface: string) => ({ device, interface: iface });

describe("linkNotes", () => {
  it("says nothing when every link of the device is declared by both ends", () => {
    const topology = {
      links: [{ a: end("leaf-01", "e49"), b: end("spine-01", "e1"), declared_by: "both", agreed: true }],
      unlinked: [],
    };

    expect(linkNotes(topology, "leaf-01")).toEqual([]);
  });

  it("names a link only one end declares, from either end", () => {
    const topology = {
      links: [{ a: end("leaf-01", "e49"), b: end("spine-01", "e1"), declared_by: "b", agreed: false }],
      unlinked: [],
    };

    expect(linkNotes(topology, "spine-01")).toEqual([
      "e1 names leaf-01 e49, which does not name it back.",
    ]);
    expect(linkNotes(topology, "leaf-01")).toEqual([
      "e49 is named by spine-01 e1, but does not name it back.",
    ]);
  });

  it("lists an interface whose peer is not a device of this use case", () => {
    const topology = {
      links: [],
      unlinked: [
        { device: "leaf-01", interface: "e1", peer_device: "carrier-pe", peer_interface: "ge-0/0/0" },
        { device: "leaf-01", interface: "e2", peer_device: "spine-01", peer_interface: null },
        { device: "leaf-02", interface: "e1", peer_device: "x", peer_interface: null },
      ],
    };

    expect(linkNotes(topology, "leaf-01")).toEqual([
      "e1 names carrier-pe ge-0/0/0, which is not drawn here.",
      "e2 names spine-01 without an interface, which is not drawn here.",
    ]);
  });
});
