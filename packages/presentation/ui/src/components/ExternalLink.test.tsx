import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ExternalLink } from "./ExternalLink";

describe("ExternalLink", () => {
  it("opens in a new tab and sends no referrer", () => {
    render(<ExternalLink href="https://github.com/anton-tvrz/snapl">Repository</ExternalLink>);

    const link = screen.getByRole("link", { name: "Repository" });
    expect(link).toHaveAttribute("href", "https://github.com/anton-tvrz/snapl");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noreferrer");
  });
});
