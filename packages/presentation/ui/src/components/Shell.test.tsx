import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { Shell } from "./Shell";

afterEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.theme;
});

describe("Shell", () => {
  it("names the use case and says the page is live and read-only", () => {
    render(
      <Shell useCase="dcfabric" mode="live">
        <p>content</p>
      </Shell>,
    );

    expect(screen.getByRole("banner")).toHaveTextContent("snapl");
    expect(screen.getByRole("banner")).toHaveTextContent("dcfabric");
    expect(screen.getByText("Live")).toBeInTheDocument();
    expect(screen.getByText("Read-only")).toBeInTheDocument();
    expect(screen.getByRole("main")).toHaveTextContent("content");
  });

  it("says when it is showing an archive", () => {
    render(
      <Shell useCase="dcfabric" mode="archive">
        <p>content</p>
      </Shell>,
    );

    expect(screen.getByText("Archive")).toBeInTheDocument();
    expect(screen.queryByText("Live")).not.toBeInTheDocument();
  });

  it("shows a mode it does not know by its raw name", () => {
    render(
      <Shell useCase="dcfabric" mode="replay">
        <p>content</p>
      </Shell>,
    );

    expect(screen.getByText("replay")).toBeInTheDocument();
  });

  it("links to the plain page", () => {
    render(
      <Shell useCase="dcfabric" mode="live">
        <p>content</p>
      </Shell>,
    );

    expect(screen.getByRole("link", { name: "Plain page" })).toHaveAttribute("href", "/plain");
  });

  it("switches and remembers the theme", async () => {
    render(
      <Shell useCase="dcfabric" mode="live">
        <p>content</p>
      </Shell>,
    );

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Theme" }), "dark");

    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem("snapl.theme")).toBe("dark");
  });

  it("has no link to the plain page in a recording, which has no server", () => {
    render(
      <Shell useCase="dcfabric" mode="snapshot">
        <p>page</p>
      </Shell>,
    );

    expect(screen.getByText("Recording")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Plain page" })).not.toBeInTheDocument();
  });
});
