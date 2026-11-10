import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MODE, MODE_ARCHIVE } from "../test/fixtures";
import type { ModeResponse } from "../api/types";
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

  it("in a snapshot, says it is a recording, when it was made, from which commit and over which range", () => {
    const snapshot = {
      ...MODE_ARCHIVE,
      mode: "snapshot",
      archive: { ...MODE_ARCHIVE.archive!, name: "snapshot", parts: [], metrics_start: "2026-11-06T21:20:48Z", metrics_end: "2026-11-07T21:20:48Z" },
    } as ModeResponse;
    render(<ModeBanner mode={snapshot} />);

    const banner = screen.getByRole("region", { name: "Recording" });
    expect(banner).toHaveTextContent(
      "A recording made 2026-11-07 21:20:48 UTC from commit 6baff39, covering 2026-11-06 21:20:48 UTC to 2026-11-07 21:20:48 UTC.",
    );
    expect(banner).toHaveTextContent("Nothing here is live, and nothing refreshes.");
  });
});
