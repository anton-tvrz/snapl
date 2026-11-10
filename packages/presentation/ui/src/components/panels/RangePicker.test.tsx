import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { NOW } from "../../test/fixtures";
import { RangePicker } from "./RangePicker";

const HOUR = 3_600_000;

describe("RangePicker", () => {
  it("offers 1 h, 24 h and 7 d, each ending at the server's now", async () => {
    const onChange = vi.fn();
    render(<RangePicker now={NOW} onChange={onChange} />);

    await userEvent.click(screen.getByRole("button", { name: "24 h" }));

    expect(onChange).toHaveBeenCalledWith({ start: new Date(NOW.getTime() - 24 * HOUR), end: NOW });
  });

  it("applies a custom start and end, given in UTC", async () => {
    const onChange = vi.fn();
    render(<RangePicker now={NOW} onChange={onChange} />);

    fireEvent.change(screen.getByLabelText("From (UTC)"), { target: { value: "2026-11-07T20:00" } });
    fireEvent.change(screen.getByLabelText("To (UTC)"), { target: { value: "2026-11-07T21:00" } });
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));

    expect(onChange).toHaveBeenCalledWith({
      start: new Date("2026-11-07T20:00:00Z"),
      end: new Date("2026-11-07T21:00:00Z"),
    });
  });

  it("refuses an end before its start without asking anything", async () => {
    const onChange = vi.fn();
    render(<RangePicker now={NOW} onChange={onChange} />);

    fireEvent.change(screen.getByLabelText("From (UTC)"), { target: { value: "2026-11-07T21:00" } });
    fireEvent.change(screen.getByLabelText("To (UTC)"), { target: { value: "2026-11-07T20:00" } });
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));

    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("The end must be after the start.");
  });

  it("says which range is shown, and goes back to following now", async () => {
    const onChange = vi.fn();
    const range = { start: new Date("2026-11-07T20:00:00Z"), end: new Date("2026-11-07T21:00:00Z") };
    render(<RangePicker now={NOW} value={range} onChange={onChange} />);

    expect(screen.getByText("2026-11-07 20:00:00 UTC to 2026-11-07 21:00:00 UTC")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Follow now" }));

    expect(onChange).toHaveBeenCalledWith(undefined);
  });

  it("says it follows now when no range is chosen", () => {
    render(<RangePicker now={NOW} onChange={vi.fn()} />);

    expect(screen.getByText("Following now: the lanes show the last 24 h, the metrics the last hour.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Follow now" })).not.toBeInTheDocument();
  });
});
