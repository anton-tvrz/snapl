import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Legend } from "./Legend";

const labels = () =>
  within(screen.getByRole("list", { name: "Drift states shown" }))
    .getAllByRole("listitem")
    .map((item) => item.querySelector("[data-label]")?.textContent);

describe("Legend", () => {
  it("lists each state shown once, in the encoding's order", () => {
    render(<Legend states={["undesired", "in_sync", "in_sync", "error", "missing"]} />);

    expect(labels()).toEqual(["In sync", "Missing intent", "Undesired config", "Scan error"]);
  });

  it("puts a state a newer server sent last, by its raw text", () => {
    render(<Legend states={["quarantined", "in_sync", "archived"]} />);

    expect(labels()).toEqual(["In sync", "archived", "quarantined"]);
  });

  it("draws the mark beside each label", () => {
    render(<Legend states={["in_sync"]} />);

    expect(screen.getByRole("listitem").querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });
});
