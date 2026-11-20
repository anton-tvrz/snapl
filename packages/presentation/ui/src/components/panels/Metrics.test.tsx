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

  // spec 014 FR-023, FR-024: the number first, the table still there.
  it("leads with the latest value in the number size, and says which series it is", () => {
    render(<MetricPanel title="Workflow duration" view={view("workflow-duration")} now={NOW} />);

    const lead = screen.getByText("2.10 s", { selector: "[data-lead]" });
    expect(lead).toHaveClass("text-number");
    expect(screen.getByRole("region", { name: "Workflow duration" })).toHaveTextContent("2.10 sDeployIntent");
  });

  it("lists each drawn series with its latest value in a legend", () => {
    render(<MetricPanel title="Workflow duration" view={view("workflow-duration")} now={NOW} />);

    const legend = screen.getByRole("list", { name: "Workflow duration: series" });
    expect(within(legend).getAllByRole("listitem").map((item) => item.textContent)).toEqual(["DeployIntent 2.10 s", "ScanDrift 1.58 s"]);
  });

  it("has no legend for a single series: the lead value is it", () => {
    render(<MetricPanel title="Worker lag" view={view("worker-lag")} now={NOW} />);

    expect(screen.getByText("26 ms", { selector: "[data-lead]" })).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Worker lag: series" })).not.toBeInTheDocument();
  });

  it("keeps the latest-values table behind a closed toggle", () => {
    render(<MetricPanel title="Workflow duration" view={view("workflow-duration")} now={NOW} />);

    const details = screen.getByText("Latest values").closest("details")!;
    expect(details).not.toHaveAttribute("open");
    expect(within(details).getByRole("table", { name: "Workflow duration: latest values" })).toBeInTheDocument();
  });

  it("reads a rare rate per minute, and says so", () => {
    render(<MetricPanel title="gNMI errors" view={view("gnmi-errors")} now={NOW} />);

    const region = screen.getByRole("region", { name: "gNMI errors" });
    expect(within(region).getByText("0/min", { selector: "[data-lead]" })).toBeInTheDocument();
    expect(region).toHaveTextContent("per minute");
  });

  // spec 015: a count's lead is its latest point with the window it covers, never a sum.
  it("leads a count of events with the latest point and the window it covers", () => {
    render(<MetricPanel title="Workflows completed (count)" kind="events" view={view("workflows-completed")} now={NOW} />);

    const region = screen.getByRole("region", { name: "Workflows completed (count)" });
    expect(within(region).getByText("1", { selector: "[data-lead]" })).toBeInTheDocument();
    expect(region).toHaveTextContent("1in the last 60 sScanDrift");
    expect(region).toHaveTextContent("count · step 15 s");
    expect(region).toHaveTextContent("events in the last 60 s");
  });

  it("leads a gauge count with its current value and no window", () => {
    render(<MetricPanel title="Worker slots in use (current value)" kind="current" view={view("worker-slots")} now={NOW} />);

    const region = screen.getByRole("region", { name: "Worker slots in use (current value)" });
    expect(within(region).getByText("6", { selector: "[data-lead]" })).toBeInTheDocument();
    expect(region).not.toHaveTextContent("in the last");
  });

  it("leads an up/down view with up or down in words", () => {
    render(<MetricPanel title="Worker up" view={view("worker-up")} now={NOW} />);

    expect(screen.getByText("up", { selector: "[data-lead]" })).toBeInTheDocument();
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

  // Spec 016 PR E2, and the decision on #312 (2026-11-20).
  describe("the network views", () => {
    it("draws the six highest of 16 interfaces and says how many it left out", () => {
      render(<MetricPanel name="interface-errors" title="Interface errors (rate)" view={view("interface-errors")} now={NOW} />);

      const region = screen.getByRole("region", { name: "Interface errors (rate)" });
      const legend = within(region).getByRole("list", { name: "Interface errors (rate): series" });
      const items = within(legend).getAllByRole("listitem").map((item) => item.textContent);
      expect(items).toHaveLength(6);
      expect(items[0]).toBe("leaf-02 ethernet-1/49 12/min");
      expect(region).toHaveTextContent("The chart draws the 6 highest of 16 series; all 16 are in the table.");
      // The table has every series, the drawn ones first with their colour.
      const rows = within(table()).getAllByRole("row").slice(1);
      expect(rows).toHaveLength(16);
      expect(rows[0]).toHaveTextContent("leaf-02 ethernet-1/49");
      expect(rows.filter((row) => row.querySelector("[aria-hidden='true']"))).toHaveLength(6);
    });

    it("says there were no discards in place of 16 flat lines", () => {
      render(<MetricPanel name="interface-discards" title="Interface discards (rate)" view={view("interface-discards")} now={NOW} />);

      const region = screen.getByRole("region", { name: "Interface discards (rate)" });
      expect(region).toHaveTextContent("No discards on any interface in this range.");
      expect(within(region).queryByRole("img")).not.toBeInTheDocument();
      expect(within(region).queryByRole("list", { name: /series/ })).not.toBeInTheDocument();
      expect(region).not.toHaveTextContent("highest");
      expect(within(table()).getAllByRole("row").slice(1)).toHaveLength(16);
    });

    it("draws traffic as one line per device, in bytes per second", () => {
      render(<MetricPanel name="interface-traffic" title="Interface traffic (rate)" view={view("interface-traffic")} now={NOW} />);

      const region = screen.getByRole("region", { name: "Interface traffic (rate)" });
      expect(within(region).getByText("9.6 kB/s", { selector: "[data-lead]" })).toBeInTheDocument();
      expect(within(region).getAllByRole("listitem")).toHaveLength(6);
      expect(region).toHaveTextContent("bytes per second · step 15 s");
      expect(region).not.toHaveTextContent("highest");
    });

    it("gives CPU in percent, with the lab note", () => {
      render(<MetricPanel name="device-cpu" title="Device CPU (current value)" kind="current" view={view("device-cpu")} now={NOW} />);

      const region = screen.getByRole("region", { name: "Device CPU (current value)" });
      expect(within(region).getByText("5%", { selector: "[data-lead]" })).toBeInTheDocument();
      expect(region).toHaveTextContent("On the lab, this is the host VM's, the same on every device");
      expect(region).toHaveTextContent("percent · step 15 s");
    });

    it("gives memory the lab note too, and traffic none", () => {
      const { unmount } = render(<MetricPanel name="device-memory" title="Device memory (current value)" view={view("device-memory")} now={NOW} />);
      expect(screen.getByRole("region", { name: "Device memory (current value)" })).toHaveTextContent("host VM's");
      unmount();

      render(<MetricPanel name="interface-traffic" title="Interface traffic (rate)" view={view("interface-traffic")} now={NOW} />);
      expect(screen.getByRole("region", { name: "Interface traffic (rate)" })).not.toHaveTextContent("host VM's");
    });
  });
});
