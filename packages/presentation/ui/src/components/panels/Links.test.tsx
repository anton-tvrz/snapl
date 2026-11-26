import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LINKS } from "../../test/fixtures";
import { Links } from "./Links";

describe("Links", () => {
  it("groups the links: stack, GitHub, API and docs", () => {
    render(<Links links={LINKS} />);

    const groups = screen.getAllByRole("group").map((g) => g.getAttribute("aria-label"));
    expect(groups).toEqual(["Stack", "GitHub", "API", "Docs"]);
    expect(within(screen.getByRole("group", { name: "Stack" })).getByRole("link", { name: "Temporal" })).toHaveAttribute(
      "href",
      "http://localhost:18034/namespaces/default/workflows",
    );
  });

  it("opens other sites in a new tab with no referrer, and this dashboard's API in place", () => {
    render(<Links links={LINKS} />);

    const repo = screen.getByRole("link", { name: "Repository" });
    expect(repo).toHaveAttribute("target", "_blank");
    expect(repo).toHaveAttribute("rel", "noreferrer");
    expect(screen.getByRole("link", { name: "OpenAPI" })).not.toHaveAttribute("target");
  });

  it("says why some links are left out, with the remedy", () => {
    const fewer = { ...LINKS, links: LINKS.links.filter((l) => l.group !== "github"), reason: "no repository is set", remedy: "export SNAPL_REPO=owner/name" };
    render(<Links links={fewer} />);

    expect(screen.getByText("no repository is set")).toBeInTheDocument();
    expect(screen.getByText("export SNAPL_REPO=owner/name")).toBeInTheDocument();
  });

  it("shows a group a newer server sent by its raw name", () => {
    const newer = { ...LINKS, links: [{ group: "grafana", label: "Dashboards", url: "http://localhost:3000" }] } as unknown as typeof LINKS;
    render(<Links links={newer} />);

    expect(screen.getByRole("group", { name: "grafana" })).toBeInTheDocument();
  });
});
