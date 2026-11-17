import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Fold } from "./Fold";

// spec 014 SC-003 (#290): a panel or a group shown as its heading until opened.
describe("Fold", () => {
  it("shows a closed panel as its heading and anchor, without its content", () => {
    render(
      <Fold id="fabric-history" title="Fabric history" open={false} onToggle={() => {}}>
        <p>four charts</p>
      </Fold>,
    );

    const region = screen.getByRole("region", { name: "Fabric history" });
    expect(region).toHaveAttribute("id", "fabric-history");
    expect(screen.getByRole("heading", { level: 2, name: "Fabric history" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show Fabric history" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("four charts")).not.toBeInTheDocument();
  });

  it("asks to open or close by its id", async () => {
    const onToggle = vi.fn();
    render(
      <Fold id="fabric-history" title="Fabric history" open={false} onToggle={onToggle}>
        <p>four charts</p>
      </Fold>,
    );

    await userEvent.click(screen.getByRole("button", { name: "Show Fabric history" }));

    expect(onToggle).toHaveBeenCalledWith("fabric-history");
  });

  it("shows an open panel's content, which carries the anchor, with a way to close it", () => {
    render(
      <Fold id="fabric-history" title="Fabric history" open onToggle={() => {}}>
        <section id="fabric-history" aria-label="Fabric history">
          four charts
        </section>
      </Fold>,
    );

    expect(screen.getByText("four charts")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Hide Fabric history" })).toHaveAttribute("aria-expanded", "true");
    expect(document.querySelectorAll("#fabric-history")).toHaveLength(1);
  });

  it("shows a closed group as its label, saying how many panels it holds", () => {
    render(
      <Fold id="metrics-worker" title="The worker" group={4} open={false} onToggle={() => {}}>
        <p>panels</p>
      </Fold>,
    );

    const group = screen.getByRole("group", { name: "The worker" });
    expect(group).toHaveAttribute("id", "metrics-worker");
    expect(screen.getByRole("heading", { level: 3, name: "The worker" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show The worker" })).toHaveTextContent("Show 4 panels");
    expect(screen.queryByText("panels")).not.toBeInTheDocument();
  });

  it("keeps an open group's label, with its content below", () => {
    render(
      <Fold id="metrics-worker" title="The worker" group={4} open onToggle={() => {}}>
        <p>panels</p>
      </Fold>,
    );

    expect(screen.getByRole("group", { name: "The worker" })).toHaveTextContent("panels");
    expect(screen.getByRole("button", { name: "Hide The worker" })).toHaveAttribute("aria-expanded", "true");
  });
});
