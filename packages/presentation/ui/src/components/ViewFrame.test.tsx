import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { View } from "../api/types";
import { ArchiveMode, PanelFrame, ViewFrame } from "./ViewFrame";

const NOW = new Date("2026-11-07T21:00:30Z");
const base = { source: "temporal", fetched_at: "2026-11-07T21:00:00Z" } as const;

function frame(view: View<unknown> | undefined) {
  return render(
    <ViewFrame title="Schedule" view={view} now={NOW}>
      <p>the schedule itself</p>
    </ViewFrame>,
  );
}

describe("ViewFrame", () => {
  it("says why it has nothing to show when its first request failed", () => {
    render(
      <ViewFrame title="Schedule" view={undefined} now={NOW} error={new Error("the dashboard server answered 500")}>
        <p>the schedule itself</p>
      </ViewFrame>,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("the dashboard server answered 500");
    expect(screen.queryByText("Loading…")).not.toBeInTheDocument();
  });

  it("is a region named by its title", () => {
    frame({ ...base, available: true, data: {} });

    expect(screen.getByRole("region", { name: "Schedule" })).toBeInTheDocument();
  });

  it("shows its content with where the data came from and how old it is", () => {
    frame({ ...base, available: true, data: {} });

    expect(screen.getByRole("heading", { name: "Schedule" })).toBeInTheDocument();
    expect(screen.getByText("the schedule itself")).toBeInTheDocument();
    expect(screen.getByText(/Temporal/)).toBeInTheDocument();
    expect(screen.getByText(/30 s ago/)).toBeInTheDocument();
  });

  it("shows the reason and the remedy in place of the content when its source is down", () => {
    frame({ source: "temporal", available: false, reason: "connection refused", remedy: "uv run invoke dev.deps" });

    expect(screen.queryByText("the schedule itself")).not.toBeInTheDocument();
    expect(screen.getByText("connection refused")).toBeInTheDocument();
    expect(screen.getByText("uv run invoke dev.deps")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Unavailable");
    expect(screen.getByRole("button", { name: "Copy command: uv run invoke dev.deps" })).toBeInTheDocument();
  });

  it("shows stale data, labelled with its age, with the reason it is stale", () => {
    frame({
      ...base,
      available: false,
      stale: true,
      reason: "rate limit spent",
      remedy: "export GITHUB_TOKEN=…",
      data: {},
    });

    expect(screen.getByText("the schedule itself")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Stale: last read 30 s ago");
    expect(screen.getByText("rate limit spent")).toBeInTheDocument();
    expect(screen.getByText("export GITHUB_TOKEN=…")).toBeInTheDocument();
  });

  it("says it is loading before the first answer", () => {
    frame(undefined);

    expect(screen.getByText("Loading…")).toBeInTheDocument();
    expect(screen.queryByText("the schedule itself")).not.toBeInTheDocument();
  });

  it("shows a source it does not know by its raw name", () => {
    frame({ source: "netbox", available: true, fetched_at: base.fetched_at, data: {} });

    expect(screen.getByText(/netbox/)).toBeInTheDocument();
  });

  it("renders a reason as text, never as markup", () => {
    frame({ source: "audit", available: false, reason: "<img src=x onerror=alert(1)>", remedy: "snapl scan" });

    expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });

  it("says a view shows only the present when a past time is being shown", () => {
    render(
      <ViewFrame title="Schedule" view={{ ...base, available: true, data: {} }} now={NOW} presentOnly>
        <p>the schedule itself</p>
      </ViewFrame>,
    );

    expect(screen.getByText("Shows only the present: this source keeps no history.")).toBeInTheDocument();
  });

  it("shows a view an archive does not hold neutrally, with how to see it live", () => {
    render(
      <ArchiveMode.Provider value={true}>
        <ViewFrame
          title="Running now"
          view={{ source: "temporal", available: false, reason: "not in this archive", remedy: "uv run snapl dashboard" }}
          now={NOW}
        >
          <p>running</p>
        </ViewFrame>
      </ArchiveMode.Provider>,
    );

    expect(screen.getByRole("status")).toHaveTextContent("Not in this archive");
    expect(screen.queryByText("Unavailable")).not.toBeInTheDocument();
    expect(screen.getByText(/To see it live/)).toBeInTheDocument();
    expect(screen.getByText("uv run snapl dashboard")).toBeInTheDocument();
    expect(screen.getByRole("status").closest(".bg-warn-surface")).toBeNull();
  });

  it("keeps the reason after 'not in this archive'", () => {
    render(
      <ArchiveMode.Provider value={true}>
        <ViewFrame
          title="CI"
          view={{ source: "ci", available: false, reason: "not in this archive: rate limited when it was made", remedy: "uv run snapl dashboard" }}
          now={NOW}
        >
          <p>ci</p>
        </ViewFrame>
      </ArchiveMode.Provider>,
    );

    expect(screen.getByText("rate limited when it was made")).toBeInTheDocument();
  });

  it("still shows other failures as failures in an archive", () => {
    render(
      <ArchiveMode.Provider value={true}>
        <ViewFrame title="Devices" view={{ source: "audit", available: false, reason: "the audit trail could not be read" }} now={NOW}>
          <p>devices</p>
        </ViewFrame>
      </ArchiveMode.Provider>,
    );

    expect(screen.getByText("Unavailable")).toBeInTheDocument();
  });

  // The one frame of spec 014 (FR-010): title left, one action right, source and age in a footer.
  it("puts the source and the age in its footer, not its header", () => {
    frame({ ...base, available: true, data: {} });

    const region = screen.getByRole("region", { name: "Schedule" });
    const footer = region.querySelector("footer")!;
    expect(footer).toHaveTextContent("Temporal · 30 s ago");
    expect(region.querySelector("header")).not.toHaveTextContent("Temporal");
    // The footer comes after the content.
    expect(screen.getByText("the schedule itself").compareDocumentPosition(footer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("has no footer before the first answer", () => {
    frame(undefined);

    expect(screen.getByRole("region", { name: "Schedule" }).querySelector("footer")).toBeNull();
  });

  it("holds one action on the right of its header", () => {
    render(
      <ViewFrame title="Runs" view={{ ...base, available: true, data: {} }} now={NOW} action={<button type="button">Expand</button>}>
        <p>runs</p>
      </ViewFrame>,
    );

    const header = screen.getByRole("region", { name: "Runs" }).querySelector("header")!;
    expect(within(header).getByRole("heading", { name: "Runs" })).toBeInTheDocument();
    expect(within(header).getByRole("button", { name: "Expand" })).toBeInTheDocument();
  });

  it("sets its title in the title size and can be linked to by id", () => {
    render(
      <ViewFrame title="Fabric" view={{ ...base, available: true, data: {} }} now={NOW} id="fabric">
        <p>fabric</p>
      </ViewFrame>,
    );

    expect(screen.getByRole("heading", { name: "Fabric" })).toHaveClass("text-title");
    expect(screen.getByRole("region", { name: "Fabric" })).toHaveAttribute("id", "fabric");
  });

  it("is lifted by the panel shadow, and keeps its border for the dark theme", () => {
    frame({ ...base, available: true, data: {} });

    const region = screen.getByRole("region", { name: "Schedule" });
    expect(region).toHaveClass("shadow-panel");
    expect(region.className).toMatch(/border/);
  });

  it("keeps a failure's reason to a readable line length", () => {
    frame({ source: "temporal", available: false, reason: "connection refused", remedy: "uv run invoke dev.deps" });

    expect(screen.getByText("connection refused")).toHaveClass("max-w-prose");
  });
});

describe("PanelFrame", () => {
  it("frames a panel that has no view of its own: title, footer text and content", () => {
    render(
      <PanelFrame title="Drift and deploys by hour" footer="Audit trail">
        <p>the table</p>
      </PanelFrame>,
    );

    const region = screen.getByRole("region", { name: "Drift and deploys by hour" });
    expect(region.querySelector("footer")).toHaveTextContent("Audit trail");
    expect(within(region).getByText("the table")).toBeInTheDocument();
  });

  it("can shrink inside a grid, so wide content scrolls inside it and never widens the page", () => {
    // Found at 360 px (spec 014 T062): a grid item is as wide as its content unless it may shrink.
    render(
      <PanelFrame title="CI">
        <p>content</p>
      </PanelFrame>,
    );

    expect(screen.getByRole("region", { name: "CI" })).toHaveClass("min-w-0");
  });

  it("has no footer when it is given none", () => {
    render(
      <PanelFrame title="Links">
        <p>links</p>
      </PanelFrame>,
    );

    expect(screen.getByRole("region", { name: "Links" }).querySelector("footer")).toBeNull();
  });
});
