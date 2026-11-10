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

describe("Runs", () => {
  it("lists every run with its type, target, start, duration, outcome and trigger", () => {
    render(<Runs runs={runsView} now={NOW} />);

    expect(rows()).toHaveLength(12);
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

    expect(rows()).toHaveLength(12);
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
