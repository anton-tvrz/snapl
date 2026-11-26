import { fireEvent, render, screen, within } from "@testing-library/react";
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

  it("in an archive, says the metrics show only the exported range and nothing follows now", () => {
    const exported = { start: new Date("2026-10-31T21:18:00Z"), end: new Date("2026-11-07T21:18:00Z") };
    render(<RangePicker now={NOW} onChange={vi.fn()} archived={exported} />);

    expect(
      screen.getByText(
        "The metrics show the exported range, 2026-10-31 21:18:00 UTC to 2026-11-07 21:18:00 UTC: an archive holds no other. The range here applies to the lanes and the history.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Up to the archive's time: the lanes show its last 24 h.")).toBeInTheDocument();
    expect(screen.queryByText(/Following now/)).not.toBeInTheDocument();
  });

  // Spec 016 T066 (#351): the network views at 15 s around each fault.
  it("in an archive with detail, lists each window as a button that picks its range", async () => {
    const exported = { start: new Date("2026-10-31T21:18:00Z"), end: new Date("2026-11-07T21:18:00Z") };
    const windows = [
      { start: new Date("2026-11-07T10:00:15Z"), end: new Date("2026-11-07T10:12:00Z") },
      { start: new Date("2026-11-06T23:40:00Z"), end: new Date("2026-11-07T00:10:00Z") },
    ];
    const onChange = vi.fn();
    render(<RangePicker now={NOW} onChange={onChange} archived={exported} windows={windows} />);

    const detail = screen.getByRole("group", { name: "Detail around faults" });
    await userEvent.click(within(detail).getByRole("button", { name: "2026-11-07 10:00 to 10:12 UTC" }));
    expect(onChange).toHaveBeenCalledWith(windows[0]);
    expect(within(detail).getByRole("button", { name: "2026-11-06 23:40 to 2026-11-07 00:10 UTC" })).toBeInTheDocument();
    expect(
      screen.getByText(
        "The metrics show the exported range, 2026-10-31 21:18:00 UTC to 2026-11-07 21:18:00 UTC. The network views also hold 15 s detail around each fault: pick a window, or a range inside one. The range here applies to the lanes and the history too.",
      ),
    ).toBeInTheDocument();
  });

  it("in an archive without detail, lists no windows", () => {
    const exported = { start: new Date("2026-10-31T21:18:00Z"), end: new Date("2026-11-07T21:18:00Z") };
    render(<RangePicker now={NOW} onChange={vi.fn()} archived={exported} windows={[]} />);

    expect(screen.queryByRole("group", { name: "Detail around faults" })).not.toBeInTheDocument();
    expect(screen.getByText(/an archive holds no other/)).toBeInTheDocument();
  });

  // spec 014 FR-027, research R9: a toolbar, with the custom fields one click away.
  it("is a toolbar that always shows the presets and the range in words", () => {
    render(<RangePicker now={NOW} onChange={vi.fn()} />);

    const bar = screen.getByRole("region", { name: "Time range" });
    expect(bar).toHaveAttribute("data-toolbar");
    expect(screen.getAllByRole("button").map((button) => button.textContent)).toEqual(["1 h", "24 h", "7 d", "Apply"]);
    expect(screen.getByText(/^Following now/)).toBeInTheDocument();
  });

  it("keeps the custom start and end behind a closed disclosure", () => {
    render(<RangePicker now={NOW} onChange={vi.fn()} />);

    const details = screen.getByText("Custom range").closest("details")!;
    expect(details).not.toHaveAttribute("open");
    expect(details).toContainElement(screen.getByLabelText("From (UTC)"));
    expect(details).toContainElement(screen.getByRole("button", { name: "Apply" }));
  });

  it("opens the disclosure when a custom range is refused, so the reason is seen", async () => {
    render(<RangePicker now={NOW} onChange={vi.fn()} />);

    fireEvent.change(screen.getByLabelText("From (UTC)"), { target: { value: "2026-11-07T21:00" } });
    fireEvent.change(screen.getByLabelText("To (UTC)"), { target: { value: "2026-11-07T20:00" } });
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));

    expect(screen.getByText("Custom range").closest("details")).toHaveAttribute("open");
  });
});
