import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { NumberCard } from "../lib/numbers";
import { NumberRow } from "./NumberRow";

const CARDS: NumberCard[] = [
  { id: "in_sync", label: "Devices in sync", state: "known", value: "5 of 6", context: "4 of 6 in sync 24 h ago.", target: "fabric" },
  { id: "open_drifts", label: "Open drifts", state: "known", value: "1", context: "On devices in intent.", target: "drift-over-time" },
  {
    id: "latest_repair",
    label: "Latest repair",
    state: "known",
    value: "13 s",
    context: "spine-01, scan to scan, repaired by an operator's reconcile, 3 d ago.",
    target: "drift-over-time",
  },
  { id: "deploys_repairs", label: "Deploys, 24 h", state: "unavailable", value: null, context: "Not read yet.", target: "history" },
];

describe("NumberRow", () => {
  it("is a named list of four cards, in order", () => {
    render(<NumberRow cards={CARDS} />);

    const list = screen.getByRole("list", { name: "The fabric in numbers" });
    const items = within(list).getAllByRole("listitem");
    expect(items.map((item) => within(item).getByRole("heading").textContent)).toEqual([
      "Devices in sync",
      "Open drifts",
      "Latest repair",
      "Deploys, 24 h",
    ]);
  });

  it("shows each value in the largest size, with its context line", () => {
    render(<NumberRow cards={CARDS} />);

    const value = screen.getByText("5 of 6");
    expect(value).toHaveClass("text-number-lg");
    expect(screen.getByText("4 of 6 in sync 24 h ago.")).toBeInTheDocument();
    expect(screen.getByText(/scan to scan/)).toBeInTheDocument();
  });

  it("links each card to the panel that holds its detail", () => {
    render(<NumberRow cards={CARDS} />);

    expect(screen.getByRole("link", { name: "Devices in sync: view more" })).toHaveAttribute("href", "#fabric");
    expect(screen.getByRole("link", { name: "Open drifts: view more" })).toHaveAttribute("href", "#drift-over-time");
    expect(screen.getByRole("link", { name: "Deploys, 24 h: view more" })).toHaveAttribute("href", "#history");
  });

  it("says in words why a card has no number, and shows no zero in its place", () => {
    render(<NumberRow cards={CARDS} />);

    const card = screen.getByRole("heading", { name: "Deploys, 24 h" }).closest("li")!;
    expect(within(card).getByText("Not read yet.")).toBeInTheDocument();
    expect(card.querySelector(".text-number-lg")).toBeNull();
    expect(card).not.toHaveTextContent(/\b0\b/);
  });

  it("keeps a zero that is a real count", () => {
    render(<NumberRow cards={[{ ...CARDS[1]!, value: "0", context: "No drift open on a device in intent." }]} />);

    expect(screen.getByText("0")).toHaveClass("text-number-lg");
  });

  it("hides each card's icon from screen readers", () => {
    render(<NumberRow cards={CARDS} />);

    const icons = screen.getByRole("list").querySelectorAll("svg");
    expect(icons).toHaveLength(4);
    icons.forEach((icon) => expect(icon).toHaveAttribute("aria-hidden", "true"));
  });

  it("wraps to two columns and then one on narrow windows", () => {
    render(<NumberRow cards={CARDS} />);

    expect(screen.getByRole("list")).toHaveClass("grid", "sm:grid-cols-2", "xl:grid-cols-4");
  });
});
