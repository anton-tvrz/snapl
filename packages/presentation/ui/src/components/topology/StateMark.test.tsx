import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DRIFT_ENCODING, driftEncoding, TONES } from "../../lib/encoding";
import { StateMark } from "./StateMark";

function mark(state: string) {
  const { container } = render(
    <svg>
      <StateMark encoding={driftEncoding(state)} r={10} />
    </svg>,
  );
  return container.querySelector("svg")!;
}

describe("StateMark", () => {
  it.each(Object.keys(DRIFT_ENCODING))("draws %s with its glyph, hidden from assistive technology", (state) => {
    const svg = mark(state);
    const glyph = svg.querySelector("text")!;

    expect(glyph.textContent).toBe(driftEncoding(state).glyph);
    expect(glyph).toHaveAttribute("aria-hidden", "true");
  });

  it("draws polygons as polygons and round shapes as circles", () => {
    expect(mark("missing").querySelector("polygon")?.getAttribute("points")?.split(" ")).toHaveLength(3);
    expect(mark("error").querySelector("polygon")?.getAttribute("points")?.split(" ")).toHaveLength(8);
    expect(mark("in_sync").querySelector("circle")).not.toBeNull();
    expect(mark("in_sync").querySelector("polygon")).toBeNull();
  });

  it("fills with its kind's colour and outlines in ink", () => {
    const fill = mark("undesired").querySelector("polygon")!;

    expect(fill).toHaveAttribute("fill", TONES.drift.fill);
    expect(fill).toHaveAttribute("stroke", "var(--ink)");
  });

  it("draws never scanned as a dashed ring on the surface, and an unknown state as a dashed pill", () => {
    const ring = mark("never_scanned").querySelector("circle")!;
    expect(ring).toHaveAttribute("fill", "var(--surface)");
    expect(ring).toHaveAttribute("stroke-dasharray");

    const pill = mark("quarantined").querySelector("rect")!;
    expect(pill).toHaveAttribute("stroke-dasharray");
    expect(mark("quarantined").querySelector("text")?.textContent).toBe("?");
  });
});
