import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ApiError } from "../../data/http";
import { METRICS, NOW } from "../../test/fixtures";
import { MetricPanel } from "./Metrics";

const view = (name: keyof typeof METRICS) => METRICS[name].metrics;
const table = () => screen.getByRole("table", { name: /latest/i });

describe("MetricPanel", () => {
  it("gives the unit, the step the server used, and each series' latest value as text", () => {
    render(<MetricPanel title="Workflow duration" view={view("workflow-duration")} now={NOW} />);

    expect(screen.getByRole("region", { name: "Workflow duration" })).toHaveTextContent("milliseconds · step 15 s");
    const rows = within(table()).getAllByRole("row").slice(1);
    expect(rows.map((row) => row.textContent)).toEqual(["DeployIntent2.10 s", "ScanDrift1.58 s"]);
  });

  it("draws durations and rates as a line chart", () => {
    render(<MetricPanel title="gNMI errors" view={view("gnmi-errors")} now={NOW} />);

    expect(screen.getByRole("img", { name: "gNMI errors over time, as lines. The latest values are in the table." })).toBeInTheDocument();
  });

  it("draws worker-up and source health as up/down strips, with gaps as no data", () => {
    render(<MetricPanel title="Dashboard sources up" view={view("source-health")} now={NOW} />);

    const strips = screen.getByRole("list", { name: "Dashboard sources up over time" });
    const temporal = within(strips).getByRole("listitem", { name: /^temporal/ });
    expect(temporal).toHaveAccessibleName(/^temporal: down 3 min 45 s of 1 h/);
    expect(temporal.querySelectorAll("[data-state='down']").length).toBeGreaterThan(0);
    const ci = within(strips).getByRole("listitem", { name: /^ci/ });
    expect(ci.querySelectorAll("[data-state='gap']").length).toBeGreaterThan(0);
    expect(within(table()).getAllByRole("row")).toHaveLength(6);
  });

  it("shows the reason and remedy in place of the chart when the view is unavailable", () => {
    const down = { ...view("worker-lag"), available: false, data: null, reason: "Prometheus is unreachable", remedy: "uv run invoke dev.deps" };
    render(<MetricPanel title="Worker lag" view={down} now={NOW} />);

    expect(screen.getByText("Prometheus is unreachable")).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("shows a refused range with the server's message", () => {
    const error = new ApiError(422, "the range may span at most 400 days");
    render(<MetricPanel title="Worker lag" view={view("worker-lag")} now={NOW} error={error} />);

    expect(screen.getByRole("alert")).toHaveTextContent("the range may span at most 400 days");
  });

  it("says there is no data, with the server's note", () => {
    const empty = { ...view("worker-lag"), data: { ...view("worker-lag").data!, series: [], note: "no samples in this range" } };
    render(<MetricPanel title="Worker lag" view={empty} now={NOW} />);

    expect(screen.getByText("No data in this range.")).toBeInTheDocument();
    expect(screen.getByText("no samples in this range")).toBeInTheDocument();
  });
});
