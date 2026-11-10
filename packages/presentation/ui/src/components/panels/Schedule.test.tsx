import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ScheduleStatus, View, WorkerStatus } from "../../api/types";
import { NOW, OVERVIEW, OVERVIEW_UP } from "../../test/fixtures";
import { Schedule } from "./Schedule";

const schedule = OVERVIEW_UP.schedule as View<ScheduleStatus>;
const worker = OVERVIEW_UP.worker as View<WorkerStatus>;
const status = schedule.data!;

function show(over: Partial<ScheduleStatus> = {}, workerView: View<WorkerStatus> = worker, presentOnly = false) {
  return render(
    <Schedule schedule={{ ...schedule, data: { ...status, ...over } }} worker={workerView} now={NOW} presentOnly={presentOnly} />,
  );
}

describe("Schedule", () => {
  it("gives the mode, the interval, the next run and the last run", () => {
    const { container } = show();

    expect(container).toHaveTextContent("Remediate every 5 min · active");
    expect(container).toHaveTextContent("Next run in 4 min (2026-11-07 21:24:48 UTC)");
    expect(container).toHaveTextContent("Last run 4 min ago: completed, 6 devices scanned, 1 drifted, 0 repaired");
  });

  it("says who paused it, when and why, and gives no next run", () => {
    const { container } = show({
      paused_by: "alice",
      paused_at: "2026-11-07T21:18:48Z",
      schedule: { ...status.schedule!, paused: true, note: "maintenance window", next_runs: [] },
    });

    expect(container).toHaveTextContent("Remediate every 5 min · paused");
    expect(container).toHaveTextContent("Paused by alice 2 min ago: maintenance window");
    expect(container).not.toHaveTextContent("Next run");
  });

  it("gives the command that creates a schedule when there is none", () => {
    show({ exists: false, schedule: null, suggested_command: "snapl schedule create --use-case dcfabric" });

    expect(screen.getByText("No drift schedule.")).toBeInTheDocument();
    expect(screen.getByText("snapl schedule create --use-case dcfabric")).toBeInTheDocument();
  });

  it("names a device whose scheduled repairs keep failing, with the count and the report-mode command", () => {
    const { container } = show({
      repeated_failures: [
        {
          device_id: "18e29d6e-3794-c788-32d5-c519c5f18e3a",
          name: "leaf-04",
          consecutive_failures: 3,
          last_failed_at: "2026-11-07T21:15:00Z",
          suggested_command: "snapl schedule mode report --use-case dcfabric",
        },
      ],
    });

    expect(container).toHaveTextContent("leaf-04: 3 scheduled repairs failed in a row.");
    expect(screen.getByText("snapl schedule mode report --use-case dcfabric")).toBeInTheDocument();
  });

  it("gives the worker's pollers, and the command when none is polling", () => {
    const { container, unmount } = show();
    expect(container).toHaveTextContent("1 worker polling snapl-orchestrator");
    unmount();

    const none = show({}, { ...worker, data: { ...worker.data!, pollers: 0 } });
    expect(none.container).toHaveTextContent("No worker is polling snapl-orchestrator.");
    expect(screen.getByText("uv run invoke orchestrator.start")).toBeInTheDocument();
  });

  it("says the worker's state is unknown when Temporal did not answer", () => {
    const { container } = render(
      <Schedule schedule={OVERVIEW.schedule as View<ScheduleStatus>} worker={OVERVIEW.worker as View<WorkerStatus>} now={NOW} />,
    );

    expect(container).toHaveTextContent("Worker state unknown");
  });

  it("does not claim there is no schedule from a stale answer, but still names failing repairs", () => {
    const stale = OVERVIEW.schedule as View<ScheduleStatus>;
    const withFailure = {
      ...stale,
      data: {
        ...stale.data!,
        repeated_failures: [
          {
            device_id: "18e29d6e-3794-c788-32d5-c519c5f18e3a",
            name: "leaf-04",
            consecutive_failures: 4,
            last_failed_at: "2026-11-07T21:15:00Z",
            suggested_command: "snapl schedule mode report --use-case dcfabric",
          },
        ],
      },
    };
    const { container } = render(
      <Schedule schedule={withFailure} worker={OVERVIEW.worker as View<WorkerStatus>} now={NOW} />,
    );

    expect(container).not.toHaveTextContent("No drift schedule");
    expect(container).toHaveTextContent("leaf-04: 4 scheduled repairs failed in a row.");
  });

  it("says it shows only the present at a past time", () => {
    const { container } = show({}, worker, true);

    expect(container).toHaveTextContent("Shows only the present");
  });
});
