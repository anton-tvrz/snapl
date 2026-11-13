// What intent says oddly about one device's links, in words, for the device
// drawer (spec 011 FR-010). Intent is shown as it is, never corrected.

interface End {
  device: string;
  interface: string;
}

interface Input {
  links: readonly { a: End; b: End; declared_by: string; agreed: boolean }[];
  unlinked: readonly { device: string; interface: string; peer_device: string; peer_interface?: string | null }[];
}

export function linkNotes(topology: Input, device: string): string[] {
  const notes: string[] = [];
  for (const link of topology.links) {
    if (link.agreed) continue;
    for (const [mine, theirs, side] of [
      [link.a, link.b, "a"],
      [link.b, link.a, "b"],
    ] as const) {
      if (mine.device !== device) continue;
      const declaredHere = link.declared_by === side;
      notes.push(
        declaredHere
          ? `${mine.interface} names ${theirs.device} ${theirs.interface}, which does not name it back.`
          : `${mine.interface} is named by ${theirs.device} ${theirs.interface}, but does not name it back.`,
      );
    }
  }
  for (const loose of topology.unlinked) {
    if (loose.device !== device) continue;
    const peer = loose.peer_interface ? `${loose.peer_device} ${loose.peer_interface}` : `${loose.peer_device} without an interface`;
    notes.push(`${loose.interface} names ${peer}, which is not drawn here.`);
  }
  return notes;
}
