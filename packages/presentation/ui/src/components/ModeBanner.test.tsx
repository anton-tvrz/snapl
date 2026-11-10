import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MODE, MODE_ARCHIVE } from "../test/fixtures";
import { ModeBanner } from "./ModeBanner";

describe("ModeBanner", () => {
  it("names the archive, when it was made and its commit, and says nothing is contacted", () => {
    render(<ModeBanner mode={MODE_ARCHIVE} />);

    const banner = screen.getByRole("region", { name: "Archive" });
    expect(banner).toHaveTextContent("snapl-history-20261107T212048Z.tar.gz");
    expect(banner).toHaveTextContent("made 2026-11-07 21:20:48 UTC from commit 6baff39");
    expect(banner).toHaveTextContent("Nothing is contacted. Ages are relative to when it was made.");
  });

  it("lists the parts the archive is missing, with their reasons", () => {
    render(<ModeBanner mode={MODE_ARCHIVE} />);

    expect(screen.getByRole("list", { name: "Missing from this archive" })).toHaveTextContent("metrics_snapshot: down");
  });

  it("says when the commit is not known", () => {
    const unknown = { ...MODE_ARCHIVE, archive: { ...MODE_ARCHIVE.archive!, snapl_commit: null } };
    render(<ModeBanner mode={unknown} />);

    expect(screen.getByRole("region", { name: "Archive" })).toHaveTextContent("from an unknown commit");
  });

  it("shows nothing when live", () => {
    const { container } = render(<ModeBanner mode={MODE} />);

    expect(container).toBeEmptyDOMElement();
  });
});
