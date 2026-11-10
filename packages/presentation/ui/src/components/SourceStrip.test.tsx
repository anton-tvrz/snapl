import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { View } from "../api/types";
import { SourceStrip } from "./SourceStrip";

const NOW = new Date("2026-11-07T21:00:30Z");
const SOURCES: View<null>[] = [
  { source: "audit", available: true, fetched_at: "2026-11-07T21:00:28Z" },
  { source: "temporal", available: false, reason: "connection refused", remedy: "uv run invoke dev.deps" },
  { source: "ci", available: false, stale: true, fetched_at: "2026-11-07T20:50:30Z", reason: "rate limit spent", remedy: "export GITHUB_TOKEN=…" },
  { source: "netbox", available: true, fetched_at: "2026-11-07T21:00:00Z" },
];

describe("SourceStrip", () => {
  it("lists every source with its state in words, not colour alone", () => {
    render(<SourceStrip sources={SOURCES} now={NOW} />);

    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(4);
    expect(items[0]).toHaveTextContent("Audit trail");
    expect(items[0]).toHaveTextContent("up");
    expect(items[0]).toHaveTextContent("just now");
    expect(items[1]).toHaveTextContent("Temporal");
    expect(items[1]).toHaveTextContent("down");
    expect(items[2]).toHaveTextContent("CI");
    expect(items[2]).toHaveTextContent("stale");
    expect(items[2]).toHaveTextContent("10 min ago");
  });

  it("gives the reason and the remedy for a source that is down", () => {
    render(<SourceStrip sources={SOURCES} now={NOW} />);

    const temporal = screen.getAllByRole("listitem")[1] as HTMLElement;
    expect(within(temporal).getByText("connection refused")).toBeInTheDocument();
    expect(within(temporal).getByText("uv run invoke dev.deps")).toBeInTheDocument();
  });

  it("shows a source it does not know by its raw name", () => {
    render(<SourceStrip sources={SOURCES} now={NOW} />);

    expect(screen.getAllByRole("listitem")[3]).toHaveTextContent("netbox");
  });

  it("says it is loading before the first answer", () => {
    render(<SourceStrip sources={undefined} now={NOW} />);

    expect(screen.getByText("Loading sources…")).toBeInTheDocument();
  });
});
