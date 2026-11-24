import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { RunView, View } from "../../api/types";
import { NOW, OVERVIEW_UP } from "../../test/fixtures";
import { Running, Runs } from "./Runs";

const runsView = OVERVIEW_UP.runs as View<RunView[]>;
const runs = runsView.data!;
const runningView = OVERVIEW_UP.running as View<RunView[]>;

const rows = () => within(screen.getByRole("table", { name: "Runs" })).getAllByRole("row").slice(1);
const withRuns = (list: RunView[]) => ({ ...runsView, data: list });
/** `count` finished runs, newest first, each its own workflow. */
const many = (count: number, target = "spine-01"): RunView[] =>
  Array.from({ length: count }, (_, i) => ({ ...runs[0]!, workflow_id: `scan-drift-${target}-${i}`, target }));

describe("Runs", () => {
  it("lists the latest runs with their type, target, start, duration, outcome and trigger", () => {
    render(<Runs runs={runsView} now={NOW} />);

    expect(rows()).toHaveLength(10);
    const deploy = rows().find((row) => row.textContent?.includes("spine-01") && row.textContent.includes("33 s ago"))!;
    expect(deploy).toHaveTextContent("DeployIntent");
    expect(deploy).toHaveTextContent("Succeeded");
    expect(deploy).toHaveTextContent("operator");
    expect(deploy).toHaveTextContent(/operator1 s$/);
  });

  it("links each run to the Temporal Web UI in a new tab, sending no referrer", () => {
    render(<Runs runs={runsView} now={NOW} />);
    const link = within(rows()[0]!).getByRole("link", { name: "ScanDrift" });

    expect(link).toHaveAttribute("href", runs[0]!.web_ui_url);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noreferrer");
  });

  it("puts running runs first, with how long they have been running", () => {
    const running: RunView = { ...runs[0]!, workflow_id: "scan-drift-live", state: "running", started_at: "2026-11-07T21:20:36.919877Z", ended_at: null, duration_s: null };
    render(<Runs runs={withRuns([...runs, running])} now={NOW} />);

    expect(rows()[0]).toHaveTextContent("Running");
    expect(rows()[0]).toHaveTextContent("12 s so far");
  });

  it("shows a failed run's reason and the actor when one is recorded", () => {
    const failed: RunView = { ...runs[1]!, state: "failed", reason: "gNMI Set rejected", actor: "alice" };
    render(<Runs runs={withRuns([failed])} now={NOW} />);

    expect(rows()[0]).toHaveTextContent("Failed");
    expect(rows()[0]).toHaveTextContent("gNMI Set rejected");
    expect(rows()[0]).toHaveTextContent("operator (alice)");
  });

  it("shows an unknown run state by its raw text", () => {
    render(<Runs runs={withRuns([{ ...runs[0]!, state: "paused" as RunView["state"] }])} now={NOW} />);

    expect(rows()[0]).toHaveTextContent("paused");
  });

  it("shows only the selected device's runs, with a way to see every device's", async () => {
    render(<Runs runs={runsView} now={NOW} device="spine-01" />);

    expect(rows()).toHaveLength(2);
    expect(screen.getByText("Only spine-01's runs.")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Show every device's runs" }));

    expect(rows()).toHaveLength(10);
    expect(screen.getByRole("button", { name: "Show all 12 runs" })).toBeInTheDocument();
  });

  // Spec 018 US4 (#305): the schedule writes two runs every 5 minutes, so the table is capped.
  it("shows the latest 10 of 25 runs, and all 25 when asked", async () => {
    const list = many(25);
    render(<Runs runs={withRuns(list)} now={NOW} />);

    expect(rows()).toHaveLength(10);
    expect(rows()[0]).toHaveTextContent(list[0]!.workflow_type);
    expect(within(rows()[9]!).getByRole("link")).toHaveAttribute("title", "scan-drift-spine-01-9");

    await userEvent.click(screen.getByRole("button", { name: "Show all 25 runs" }));

    expect(rows()).toHaveLength(25);
    expect(screen.queryByRole("button", { name: /^Show all/ })).not.toBeInTheDocument();
  });

  it("has no button with 10 runs or fewer", () => {
    render(<Runs runs={withRuns(many(10))} now={NOW} />);

    expect(rows()).toHaveLength(10);
    expect(screen.queryByRole("button", { name: /^Show all/ })).not.toBeInTheDocument();
  });

  it("never hides a running run", () => {
    const running = Array.from({ length: 12 }, (_, i): RunView => ({
      ...runs[0]!,
      workflow_id: `scan-drift-live-${i}`,
      state: "running",
      ended_at: null,
      duration_s: null,
    }));
    render(<Runs runs={withRuns([...many(5), ...running])} now={NOW} />);

    expect(rows()).toHaveLength(12);
    for (const row of rows()) expect(row).toHaveTextContent("Running");
    expect(screen.getByRole("button", { name: "Show all 17 runs" })).toBeInTheDocument();
  });

  it("caps the selected device's runs, not every device's", async () => {
    render(<Runs runs={withRuns([...many(12, "leaf-01"), ...many(5, "spine-02")])} now={NOW} device="leaf-01" />);

    expect(rows()).toHaveLength(10);
    for (const row of rows()) expect(row).toHaveTextContent("leaf-01");
    expect(screen.getByRole("button", { name: "Show all 12 runs" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Show every device's runs" }));

    expect(rows()).toHaveLength(10);
    await userEvent.click(screen.getByRole("button", { name: "Show all 17 runs" }));
    expect(rows()).toHaveLength(17);
  });

  it("says when there is no run", () => {
    render(<Runs runs={withRuns([])} now={NOW} />);

    expect(screen.getByText("No runs recorded yet.")).toBeInTheDocument();
  });
});

describe("Running", () => {
  it("lists what is running now, with how long it has run", () => {
    const { container } = render(<Running running={runningView} now={NOW} />);

    expect(screen.getByRole("link", { name: "ScanDrift" })).toHaveAttribute("rel", "noreferrer");
    expect(container).toHaveTextContent("12 s so far");
  });

  it("says when nothing is running", () => {
    render(<Running running={{ ...runningView, data: [] }} now={NOW} />);

    expect(screen.getByText("Nothing is running.")).toBeInTheDocument();
  });

  it("says it shows only the present at a past time", () => {
    const { container } = render(<Running running={runningView} now={NOW} presentOnly />);

    expect(container).toHaveTextContent("Shows only the present");
  });
});

describe("a removal's detail (spec 013 FR-016)", () => {
  const PORT = "/interface[name=ethernet-1/7]";
  const BINDING = "/network-instance[name=default]/interface[name=ethernet-1/7.0]";
  const base: RunView = {
    ...runs[0]!,
    workflow_id: "remove-config-x",
    workflow_type: "RemoveConfig",
    target: "leaf-01",
    trigger: "operator",
    actor: "anton",
    state: "succeeded",
    reason: null,
  };
  const removal = (over: Partial<NonNullable<RunView["removal"]>>, run: Partial<RunView> = {}): RunView => ({
    ...base,
    ...run,
    removal: {
      actor: "anton",
      confirmed_paths: [PORT, BINDING],
      deleted_paths: [],
      plan_paths: [],
      device_response: null,
      gone: [],
      remaining: [],
      new_forward: [],
      still_refused: [],
      reason: null,
      detail: null,
      ...over,
    },
  });
  const detail = () => screen.getByRole("region", { name: "Removal remove-config-x" });

  it("shows who confirmed it, what it deleted, the response and that the scan after agrees", () => {
    const run = removal({
      deleted_paths: [PORT, BINDING],
      gone: [PORT, BINDING],
      still_refused: ["/routing-policy/policy[name=c-probe]/default_action"],
      device_response: "{'timestamp': 1}",
      reason: "succeeded",
    });
    render(<Runs runs={withRuns([run])} now={NOW} />);

    expect(detail()).toHaveTextContent("Confirmed by anton: 2 paths");
    expect(within(detail()).getByText(PORT)).toBeInTheDocument();
    expect(within(detail()).getByText(BINDING)).toBeInTheDocument();
    expect(detail()).toHaveTextContent("The verifying scan finds none of them.");
    expect(detail()).toHaveTextContent("Device response: {'timestamp': 1}");
    expect(detail()).toHaveTextContent("Left in place by design: /routing-policy/policy[name=c-probe]/default_action");
  });

  it("shows what the verifying scan still found", () => {
    const run = removal(
      { deleted_paths: [PORT], gone: [], remaining: [PORT], new_forward: ["/interface[name=ethernet-1/2]/enabled"], reason: "verification_failed" },
      { state: "failed", reason: "verification_failed" },
    );
    render(<Runs runs={withRuns([run])} now={NOW} />);

    expect(detail()).toHaveTextContent(`Still there: ${PORT}`);
    expect(detail()).toHaveTextContent("Newly missing or different from intent: /interface[name=ethernet-1/2]/enabled");
  });

  it.each([
    ["plan_changed", "The device changed after the preview; nothing was removed."],
    ["deploy_in_flight", "A deploy to the device was running; nothing was removed."],
    ["remove_failed", "The device rejected the removal; nothing was removed."],
  ])("says why a removal that ended %s wrote nothing", (reason, text) => {
    render(<Runs runs={withRuns([removal({ reason, detail: "the detail" }, { state: "failed", reason })])} now={NOW} />);

    expect(detail()).toHaveTextContent(text);
    expect(detail()).toHaveTextContent("the detail");
  });

  it("shows the new plan when the device changed", () => {
    render(<Runs runs={withRuns([removal({ reason: "plan_changed", plan_paths: [PORT] }, { state: "failed" })])} now={NOW} />);

    expect(detail()).toHaveTextContent(`The plan now: ${PORT}`);
  });

  it("shows a removal still running by what was confirmed", () => {
    render(<Runs runs={withRuns([removal({}, { state: "running", ended_at: null })])} now={NOW} />);

    expect(detail()).toHaveTextContent("Confirmed by anton: 2 paths");
    expect(detail()).toHaveTextContent("Not finished.");
  });

  it("shows no removal detail for other runs, and offers no way to start one", () => {
    render(<Runs runs={withRuns([base, runs[0]!])} now={NOW} />);

    expect(screen.queryByRole("region", { name: /^Removal / })).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
