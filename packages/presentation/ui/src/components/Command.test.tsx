import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Command, Recording } from "./Command";

afterEach(() => {
  vi.restoreAllMocks();
});

function stubClipboard(writeText: (text: string) => Promise<void>) {
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
}

describe("Command", () => {
  it("shows the whole command as text", () => {
    render(<Command text="snapl reconcile leaf-01" />);

    expect(screen.getByText("snapl reconcile leaf-01")).toBeInTheDocument();
  });

  it("copies the command and says so", async () => {
    const writeText = vi.fn(async () => {});
    stubClipboard(writeText);
    render(<Command text="snapl reconcile leaf-01" />);

    await userEvent.click(screen.getByRole("button", { name: "Copy command: snapl reconcile leaf-01" }));

    expect(writeText).toHaveBeenCalledExactlyOnceWith("snapl reconcile leaf-01");
    expect(await screen.findByText("Copied")).toBeInTheDocument();
  });

  it("only copies: it makes no request and runs nothing", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    stubClipboard(async () => {});
    render(<Command text="snapl schedule mode report --use-case dcfabric" />);

    await userEvent.click(screen.getByRole("button"));

    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("says when the browser refuses the clipboard, and the command stays selectable", async () => {
    stubClipboard(async () => {
      throw new Error("denied");
    });
    render(<Command text="uv run invoke dev.deps" />);

    await userEvent.click(screen.getByRole("button"));

    expect(await screen.findByText("Copy failed: select the text")).toBeInTheDocument();
    expect(screen.getByText("uv run invoke dev.deps")).toBeInTheDocument();
  });

  it("offers no copy control when told not to (a recording)", () => {
    render(<Command text="snapl reconcile leaf-01" copy={false} />);

    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByText("snapl reconcile leaf-01")).toBeInTheDocument();
  });

  it("in a recording, says it is what the operator could run, with nothing to copy", () => {
    render(
      <Recording.Provider value={true}>
        <Command text="snapl reconcile spine-01" />
      </Recording.Provider>,
    );

    expect(screen.getByText("The operator could run:")).toBeInTheDocument();
    expect(screen.getByText("snapl reconcile spine-01")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
