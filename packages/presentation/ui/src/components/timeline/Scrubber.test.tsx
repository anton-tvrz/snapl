import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { SCAN_STOPS } from "../../test/fixtures";
import { Scrubber } from "./Scrubber";

const stops = SCAN_STOPS.map((s) => new Date(s));
const domain = { start: new Date("2026-11-07T21:18:00Z"), end: new Date("2026-11-07T21:20:40Z") };

function Harness({ initial, onChange }: { initial?: Date; onChange?: (value?: Date) => void }) {
  const [value, setValue] = useState(initial);
  return (
    <Scrubber
      domain={domain}
      stops={stops}
      value={value}
      onChange={(next) => {
        setValue(next);
        onChange?.(next);
      }}
    />
  );
}

const slider = () => screen.getByRole("slider", { name: "As of" });

describe("Scrubber", () => {
  it("reads as now until it is moved", () => {
    render(<Harness />);

    expect(slider()).toHaveAttribute("aria-valuetext", "Now");
    expect(slider()).toHaveAttribute("aria-valuenow", String(domain.end.getTime()));
  });

  it("steps one scan per arrow key, from now back to the last scan", async () => {
    render(<Harness />);
    slider().focus();

    await userEvent.keyboard("{ArrowLeft}");
    expect(slider()).toHaveAttribute("aria-valuetext", "As of 2026-11-07 21:20:18 UTC");
    await userEvent.keyboard("{ArrowLeft}");
    expect(slider()).toHaveAttribute("aria-valuetext", "As of 2026-11-07 21:20:15 UTC");
    await userEvent.keyboard("{ArrowRight}");
    expect(slider()).toHaveAttribute("aria-valuenow", String(stops[2]!.getTime()));
    await userEvent.keyboard("{ArrowRight}");
    expect(slider()).toHaveAttribute("aria-valuetext", "Now");
  });

  it("goes to the range's start with Home and back to now with End", async () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    slider().focus();

    await userEvent.keyboard("{Home}");
    expect(onChange).toHaveBeenLastCalledWith(domain.start);
    await userEvent.keyboard("{End}");
    expect(onChange).toHaveBeenLastCalledWith(undefined);
  });

  it("follows the pointer and sets the time where it is let go", () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    const track = slider();
    track.getBoundingClientRect = () => ({ left: 100, width: 1000, top: 0, height: 20 }) as DOMRect;
    const handle = track.querySelector("[data-handle]") as HTMLElement;

    fireEvent.pointerDown(track, { clientX: 600, pointerId: 1 });
    expect(parseFloat(handle.style.left)).toBe(50);
    fireEvent.pointerMove(track, { clientX: 350, pointerId: 1 });
    expect(parseFloat(handle.style.left)).toBe(25);
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.pointerUp(track, { clientX: 350, pointerId: 1 });

    const quarter = domain.start.getTime() + (domain.end.getTime() - domain.start.getTime()) / 4;
    expect(onChange).toHaveBeenCalledWith(new Date(quarter));
  });

  it("returns to now when let go at the right end", () => {
    const onChange = vi.fn();
    render(<Harness initial={stops[0]} onChange={onChange} />);
    const track = slider();
    track.getBoundingClientRect = () => ({ left: 0, width: 500, top: 0, height: 20 }) as DOMRect;

    fireEvent.pointerDown(track, { clientX: 520, pointerId: 1 });
    fireEvent.pointerUp(track, { clientX: 520, pointerId: 1 });

    expect(onChange).toHaveBeenCalledWith(undefined);
  });

  it("also steps with the up and down arrows, and ignores other keys", async () => {
    const onChange = vi.fn();
    render(<Harness initial={stops[1]} onChange={onChange} />);
    slider().focus();

    await userEvent.keyboard("{ArrowDown}");
    expect(onChange).toHaveBeenLastCalledWith(stops[0]);
    await userEvent.keyboard("{ArrowUp}");
    expect(onChange).toHaveBeenLastCalledWith(stops[1]);
    await userEvent.keyboard("a");
    expect(onChange).toHaveBeenCalledTimes(2);
  });

  it("leaves the time alone when a drag is cancelled, or the pointer only passes over", () => {
    const onChange = vi.fn();
    render(<Harness initial={stops[0]} onChange={onChange} />);
    const track = slider();
    track.getBoundingClientRect = () => ({ left: 0, width: 1000, top: 0, height: 20 }) as DOMRect;

    fireEvent.pointerMove(track, { clientX: 300, pointerId: 1 });
    fireEvent.pointerUp(track, { clientX: 300, pointerId: 1 });
    fireEvent.pointerDown(track, { clientX: 300, pointerId: 1 });
    fireEvent.pointerCancel(track, { pointerId: 1 });

    expect(onChange).not.toHaveBeenCalled();
    expect(slider()).toHaveAttribute("aria-valuenow", String(stops[0]!.getTime()));
  });
});
