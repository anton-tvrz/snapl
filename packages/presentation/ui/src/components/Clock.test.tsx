import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Clock } from "./Clock";

const AT = new Date("2026-11-07T21:20:48.919Z");

beforeEach(() => {
  vi.useFakeTimers();
  // The browser's clock is wrong on purpose: the page's time is the server's.
  vi.setSystemTime(new Date("2031-01-01T00:00:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("Clock", () => {
  it("shows the page's time in UTC, in a monospace face, starting from the server's time", () => {
    render(<Clock at={AT} running />);

    const clock = screen.getByRole("time");
    expect(clock).toHaveTextContent("21:20:48 UTC");
    expect(clock).toHaveClass("font-mono");
    expect(clock).toHaveAttribute("datetime", "2026-11-07T21:20:48.000Z");
    expect(clock).toHaveAccessibleName("The time shown: 2026-11-07 21:20:48 UTC");
  });

  it("advances by elapsed time when it is running", () => {
    render(<Clock at={AT} running />);

    act(() => vi.advanceTimersByTime(3000));

    expect(screen.getByRole("time")).toHaveTextContent("21:20:51 UTC");
  });

  it("starts again from a newer server time", () => {
    const { rerender } = render(<Clock at={AT} running />);
    act(() => vi.advanceTimersByTime(3000));

    rerender(<Clock at={new Date("2026-11-07T21:21:00Z")} running />);

    expect(screen.getByRole("time")).toHaveTextContent("21:21:00 UTC");
  });

  it("stands still in an archive, a recording or at a past time", () => {
    render(<Clock at={AT} running={false} />);

    act(() => vi.advanceTimersByTime(60_000));

    expect(screen.getByRole("time")).toHaveTextContent("21:20:48 UTC");
    expect(screen.getByRole("time")).toHaveAttribute("data-running", "false");
  });

  it("shows minutes only, and changes nothing each second, when the viewer asks for less motion", () => {
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: query.includes("reduce"),
      addEventListener: () => {},
      removeEventListener: () => {},
    }));
    render(<Clock at={AT} running />);

    expect(screen.getByRole("time")).toHaveTextContent("21:20 UTC");
    act(() => vi.advanceTimersByTime(5000));
    expect(screen.getByRole("time")).toHaveTextContent("21:20 UTC");
  });

  it("shows nothing before the page knows its time", () => {
    render(<Clock at={undefined} running />);

    expect(screen.queryByRole("time")).not.toBeInTheDocument();
  });
});
