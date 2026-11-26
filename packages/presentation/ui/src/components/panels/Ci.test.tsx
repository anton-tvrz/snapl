import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { CiStatus, View } from "../../api/types";
import { NOW, OVERVIEW, OVERVIEW_UP } from "../../test/fixtures";
import { Ci } from "./Ci";

const ci = OVERVIEW_UP.ci as View<CiStatus>;
const rows = () => within(screen.getByRole("table", { name: "CI" })).getAllByRole("row").slice(1);

describe("Ci", () => {
  it("puts main first, then each PR with its gate", () => {
    render(<Ci ci={ci} now={NOW} />);

    expect(rows().map((row) => (row as HTMLTableRowElement).cells[0]!.textContent)).toEqual([
      "main",
      "#212 feat(presentation): dashboard panels",
      "#210 docs: dashboard guide",
    ]);
    expect(rows()[0]).toHaveTextContent("Passed");
    expect(rows()[1]).toHaveTextContent("Failed");
  });

  it("links each target's run and names the failed job with its link, in a new tab", () => {
    render(<Ci ci={ci} now={NOW} />);
    const pr = rows()[1]!;

    expect(within(pr).getByRole("link", { name: "#212 feat(presentation): dashboard panels" })).toHaveAttribute(
      "href",
      "https://github.com/anton-tvrz/snapl/actions/runs/200",
    );
    const failed = within(pr).getByRole("link", { name: "Unit Tests" });
    expect(failed).toHaveAttribute("href", "https://github.com/anton-tvrz/snapl/actions/runs/200/job/201");
    expect(failed).toHaveAttribute("target", "_blank");
    expect(failed).toHaveAttribute("rel", "noreferrer");
    expect(within(pr).queryByRole("link", { name: "Dashboard UI" })).not.toBeInTheDocument();
  });

  it("gives main's coverage, or says none was reported", () => {
    const { unmount } = render(<Ci ci={ci} now={NOW} />);
    expect(rows()[0]).toHaveTextContent("91.4%");
    unmount();

    const none = { ...ci, data: { ...ci.data!, targets: [{ ...ci.data!.targets[0]!, coverage_pct: null }] } };
    render(<Ci ci={none} now={NOW} />);
    expect(rows()[0]).toHaveTextContent("none reported");
  });

  it("names the repository and says how many PRs were left out without a token, with the remedy", () => {
    const { container } = render(<Ci ci={ci} now={NOW} />);

    expect(container).toHaveTextContent("anton-tvrz/snapl");
    expect(container).toHaveTextContent("3 more PRs were not checked without a token.");
    expect(screen.getByText("export GITHUB_TOKEN=…")).toBeInTheDocument();
  });

  it("shows the reason and remedy when CI is unavailable, and present-only at a past time", () => {
    const { container } = render(<Ci ci={OVERVIEW.ci as View<CiStatus>} now={NOW} presentOnly />);

    expect(container).toHaveTextContent("ci is not part of the fixture server");
    expect(container).toHaveTextContent("Shows only the present");
  });
});
