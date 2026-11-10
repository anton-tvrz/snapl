import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TIMELINE, TIMELINE_AT_SCAN_2 } from "../../test/fixtures";
import { Headline } from "./Headline";

const timeline = TIMELINE.timeline.data!;
const NOW = new Date(timeline.end);

describe("Headline", () => {
  it("gives spine-01's drift scan to scan, and what repaired it", () => {
    const { container } = render(<Headline timeline={timeline} asOf={NOW} />);

    expect(screen.getByText("5 s")).toBeInTheDocument();
    expect(container).toHaveTextContent("repaired by an operator's reconcile");
    expect(container).toHaveTextContent("1 drift open: leaf-01 for 35 s.");
    expect(container.textContent).not.toMatch(/detect/i);
  });

  it("says no drift has been repaired yet", () => {
    const past = TIMELINE_AT_SCAN_2.timeline.data!;
    const { container } = render(<Headline timeline={past} asOf={new Date(past.end)} />);

    expect(container).toHaveTextContent("No drift has been repaired yet.");
    expect(container).toHaveTextContent("2 drifts open");
  });

  it("names an unknown repair as unknown", () => {
    const unknown = { ...timeline, latest_closed: { ...timeline.latest_closed!, closed_by: null } };
    const { container } = render(<Headline timeline={unknown} asOf={NOW} />);

    expect(container).toHaveTextContent("repaired outside snapl, or intent changed (unknown)");
  });

  it("says no drift has been recorded on an empty trail", () => {
    const empty = { ...timeline, latest_closed: null, episodes: [], open_count: 0 };
    const { container } = render(<Headline timeline={empty} asOf={NOW} />);

    expect(container).toHaveTextContent("No drift has been recorded.");
  });
});
