import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { GroupLabel } from "./GroupLabel";

describe("GroupLabel", () => {
  it("is a second-level heading with its text", () => {
    render(<GroupLabel>Metrics</GroupLabel>);

    expect(screen.getByRole("heading", { level: 2, name: "Metrics" })).toBeInTheDocument();
  });

  it("is small, uppercase and letter-spaced, in the metadata size", () => {
    render(<GroupLabel>Sources</GroupLabel>);

    const heading = screen.getByRole("heading", { name: "Sources" });
    expect(heading).toHaveClass("text-meta", "uppercase", "tracking-widest");
  });

  it("can be a third-level heading inside a section", () => {
    render(<GroupLabel level={3}>The worker</GroupLabel>);

    expect(screen.getByRole("heading", { level: 3, name: "The worker" })).toBeInTheDocument();
  });
});
