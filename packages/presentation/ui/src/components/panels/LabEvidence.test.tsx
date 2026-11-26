import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { LabEvidence as Evidence, View } from "../../api/types";
import { NOW, OVERVIEW, OVERVIEW_UP } from "../../test/fixtures";
import { LabEvidence } from "./LabEvidence";

const lab = OVERVIEW_UP.lab as View<Evidence>;
const suite = (name: string) => screen.getByRole("group", { name });

describe("LabEvidence", () => {
  it("gives the latest run per suite with its result, counts, commit and age", () => {
    render(<LabEvidence lab={lab} now={NOW} />);

    expect(suite("e2e")).toHaveTextContent("Passed: 14 passed, 0 failed, 1 skipped");
    expect(suite("e2e")).toHaveTextContent("at bbbbbbbb on main · 1 h ago");
    expect(suite("integration")).toHaveTextContent("Failed: 20 passed, 2 failed, 0 skipped");
    expect(suite("integration")).toHaveTextContent("with uncommitted changes");
  });

  it("says how many commits main has had since, and how the run was matched", () => {
    render(<LabEvidence lab={lab} now={NOW} />);

    expect(suite("e2e")).toHaveTextContent("3 commits on main since this run (matched by ancestor)");
  });

  it("says unknown, with the reason, when the count cannot be known", () => {
    render(<LabEvidence lab={lab} now={NOW} />);

    expect(suite("integration")).toHaveTextContent(
      "Commits on main since this run: unknown (commit cccccccc is not in the local history)",
    );
  });

  it("lists incomplete and unreadable runs with their problem", () => {
    const { container } = render(<LabEvidence lab={lab} now={NOW} />);

    expect(container).toHaveTextContent("20261107T190000Z-e2e: Incomplete");
    expect(container).toHaveTextContent("20261105T120000Z-integration: Unreadable (run.json is not JSON)");
  });

  it("gives the command that records a run when there is none", () => {
    const empty = { ...lab, data: { latest: {}, commits_on_main_since: {}, runs: [] } };
    render(<LabEvidence lab={empty} now={NOW} />);

    expect(screen.getByText("No live lab run recorded yet. Record one with:")).toBeInTheDocument();
    expect(screen.getByText("uv run invoke test-e2e")).toBeInTheDocument();
  });

  it("shows the reason and remedy when lab runs are unavailable, and present-only at a past time", () => {
    const { container } = render(<LabEvidence lab={OVERVIEW.lab as View<Evidence>} now={NOW} presentOnly />);

    expect(container).toHaveTextContent("lab_runs is not part of the fixture server");
    expect(container).toHaveTextContent("Shows only the present");
  });
});
