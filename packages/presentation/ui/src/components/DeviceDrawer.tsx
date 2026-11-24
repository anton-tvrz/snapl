import { useEffect, useRef } from "react";

import type {
  DeviceHealthView,
  DeviceNetwork,
  DeviceView,
  FabricNetwork,
  InterfaceState,
  RemovalPreviewView,
  RunView,
  SessionState,
  View,
} from "../api/types";
import type { RunId } from "../data/provider";
import { driftEncoding, runEncoding, TONES } from "../lib/encoding";
import { healthMark } from "../lib/health";
import { formatValue, viewNote } from "../lib/metrics";
import { ageText, utcText } from "../lib/time";
import { Command } from "./Command";
import { StateMark } from "./topology/StateMark";

interface Props {
  device: DeviceView;
  /** Recent runs, of any target. This device's are picked out. */
  runs: readonly RunView[];
  /** What intent says oddly about this device's links, if anything. */
  linkNotes: readonly string[];
  now: Date;
  onClose: () => void;
  /** False in a recording, where the viewer cannot act on the fabric. */
  copy?: boolean;
  /** Shows a run as the loop. Without it, the runs are not buttons. */
  onSelectRun?: (run: RunId) => void;
  /** The run the loop shows, if one was chosen. */
  selectedRun?: RunId;
  /** `/network` for the page (spec 016): this device's entry is picked out. Without it, nothing is shown. */
  network?: View<FabricNetwork>;
}

function Paths({ title, paths }: { title: string; paths: readonly string[] }) {
  if (paths.length === 0) return null;
  return (
    <section aria-label={title} className="mt-4">
      <h3 className="text-meta font-semibold text-muted">
        {title} ({paths.length})
      </h3>
      <ul className="mt-2 space-y-2">
        {paths.map((path) => (
          <li key={path} className="chip rounded bg-sunken px-2 py-1 font-mono text-meta break-all">
            {path}
          </li>
        ))}
      </ul>
    </section>
  );
}

/** "2 deletes, 1 refused": what a removal preview found. */
function previewCounts(preview: RemovalPreviewView): string {
  const deletes = preview.plan_paths.length;
  return `${deletes} ${deletes === 1 ? "delete" : "deletes"}, ${preview.refused_paths.length} refused`;
}

/** The device's health (spec 016): its state, since when, and why. Beside the drift state, never instead of it. */
function Health({ health, now }: { health: DeviceHealthView; now: Date }) {
  const mark = healthMark(health.state);
  const reasons = health.reasons ?? [];
  return (
    <section aria-label="Health" className="mt-4">
      <h3 className="text-meta font-semibold text-muted">Health</h3>
      <p className="mt-2 flex flex-wrap items-baseline gap-x-2">
        {mark.glyph && (
          <span
            aria-hidden="true"
            className="chip rounded px-1.5 font-semibold"
            style={{ backgroundColor: TONES[mark.tone].fill, color: TONES[mark.tone].onFill }}
          >
            {mark.glyph}
          </span>
        )}
        <span className="font-semibold">{mark.label}</span>
        <span className="text-meta text-muted">since {ageText(health.since, now)}</span>
      </p>
      {reasons.length > 0 && (
        <ul className="mt-2 space-y-2">
          {reasons.map((reason) => (
            <li key={reason.text} className="break-words">
              {reason.text}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** A red "!" beside a value that is not as intent expects; the expectation is in its title. */
function Flag({ title }: { title: string }) {
  return (
    <span
      aria-hidden="true"
      title={title}
      className="chip mr-1 rounded px-1.5 font-semibold"
      style={{ backgroundColor: TONES.fail.fill, color: TONES.fail.onFill }}
    >
      !
    </span>
  );
}

/** "up", "down" flagged when intent expects up (the expectation is read out, not shown), or "no data". */
function InterfaceStateCell({ item }: { item: InterfaceState }) {
  if (item.up === null || item.up === undefined) return <td className="py-2 pr-4 text-muted">no data</td>;
  const actual = item.up ? "up" : "down";
  const off = actual !== item.expected;
  return (
    <td className="py-2 pr-4 whitespace-nowrap">
      {off && <Flag title={`Expected ${item.expected}`} />}
      {actual}
      {off && <span className="sr-only">, expected {item.expected}</span>}
    </td>
  );
}

/** "errors in 12/min, discards out 3/min": only the figures that are not zero or missing. */
function lossText(item: InterfaceState): string {
  const figures: [string, number | null | undefined][] = [
    ["errors in", item.in_errors_ps],
    ["errors out", item.out_errors_ps],
    ["discards in", item.in_discards_ps],
    ["discards out", item.out_discards_ps],
  ];
  return figures
    .filter(([, value]) => value !== null && value !== undefined && value !== 0)
    .map(([words, value]) => `${words} ${formatValue(value, "per_second")}`)
    .join(", ");
}

const TH = "cell py-1 pr-4 font-medium";

function Interfaces({ items }: { items: readonly InterfaceState[] }) {
  return (
    <section aria-label="Interfaces" className="mt-4">
      <h3 className="text-meta font-semibold text-muted">Interfaces ({items.length})</h3>
      {/* A narrow window scrolls the table, never the page. */}
      <div className="mt-2 overflow-x-auto">
        <table className="w-full text-left">
          <thead className="text-meta text-muted">
            <tr>
              <th className={TH}>Interface</th>
              <th className={TH}>State</th>
              <th className={TH}>In</th>
              <th className="cell py-1 font-medium">Out</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => {
              const loss = lossText(item);
              return (
                <tr key={item.name} className="border-t border-line">
                  <td className="py-2 pr-4">
                    <span className="font-mono text-meta whitespace-nowrap">{item.name}</span>
                    {/* Errors and discards under the name, only when not zero: a column of them wrapped every cell. */}
                    {loss && <span className="block text-meta">{loss}</span>}
                  </td>
                  <InterfaceStateCell item={item} />
                  <td className="py-2 pr-4 tabular-nums whitespace-nowrap">{formatValue(item.in_bps, "bytes_per_second")}</td>
                  <td className="py-2 tabular-nums whitespace-nowrap">{formatValue(item.out_bps, "bytes_per_second")}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/** "established", "not established" flagged when intent expects it (read out, not shown), or "no data". */
function SessionStateCell({ item }: { item: SessionState }) {
  if (item.established === null || item.established === undefined) return <td className="py-2 pr-4 text-muted">no data</td>;
  const actual = item.established ? "established" : "not established";
  const off = item.expected === "established" && !item.established;
  return (
    <td className="py-2 pr-4 whitespace-nowrap">
      {off && <Flag title={`Expected ${item.expected}`} />}
      {actual}
      {off && <span className="sr-only">, expected {item.expected}</span>}
    </td>
  );
}

function Sessions({ items }: { items: readonly SessionState[] }) {
  return (
    <section aria-label="BGP sessions" className="mt-4">
      <h3 className="text-meta font-semibold text-muted">BGP sessions ({items.length})</h3>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full text-left">
          <thead className="text-meta text-muted">
            <tr>
              <th className={TH}>Peer</th>
              <th className={TH}>State</th>
              <th className="cell py-1 font-medium">Prefixes received</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => {
              const prefixes = item.prefixes_received;
              const missing = prefixes === null || prefixes === undefined;
              return (
                <tr key={item.peer} className="border-t border-line">
                  <td className="py-2 pr-4 font-mono text-meta whitespace-nowrap">{item.peer}</td>
                  <SessionStateCell item={item} />
                  <td className={`py-2 tabular-nums ${missing ? "text-muted" : ""}`}>{missing ? "no data" : prefixes}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/** The device's interfaces, sessions, CPU and memory now, from its entry in `/network` (spec 016 FR-011). */
function Network({ view, device }: { view: View<FabricNetwork>; device: DeviceView }) {
  if (!view.available || !view.data) {
    return (
      <section aria-label="Network" className="mt-4">
        <h3 className="text-meta font-semibold text-muted">Interfaces, sessions, CPU and memory</h3>
        <p className="mt-2 max-w-prose break-words text-muted">{view.reason ?? "Not available."}</p>
        {view.remedy && (
          <p className="mt-2">
            <Command text={view.remedy} copy={false} />
          </p>
        )}
      </section>
    );
  }
  const entry: DeviceNetwork | undefined = view.data.devices.find(
    (d) => d.device_id === device.device_id || d.name === device.name,
  );
  if (!entry) return null;
  const interfaces = entry.interfaces ?? [];
  const sessions = entry.sessions ?? [];
  return (
    <>
      {interfaces.length > 0 && <Interfaces items={interfaces} />}
      {sessions.length > 0 && <Sessions items={sessions} />}
      <section aria-label="CPU and memory" className="mt-4">
        <h3 className="text-meta font-semibold text-muted">CPU and memory</h3>
        <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-2">
          <dt className="text-muted">CPU</dt>
          <dd className="tabular-nums">{formatValue(entry.cpu_percent, "percent")}</dd>
          <dt className="text-muted">Memory</dt>
          <dd className="tabular-nums">{formatValue(entry.memory_percent, "percent")}</dd>
        </dl>
        <p className="mt-2 text-meta text-muted">{viewNote("device-cpu")}</p>
      </section>
    </>
  );
}

/**
 * One device's detail (spec 011 FR-016): how it differs from intent, when it
 * was last scanned and deployed, its recent runs, and the command that would
 * reconcile it. Everything recorded from the device is rendered as text.
 * Its health comes first (spec 016 FR-011); its interfaces, sessions, CPU and
 * memory follow what snapl itself found.
 */
export function DeviceDrawer({ device, runs, linkNotes, now, onClose, copy = true, onSelectRun, selectedRun, network }: Props) {
  const encoding = driftEncoding(device.drift_state);
  const close = useRef<HTMLButtonElement>(null);
  const own = runs.filter((run) => run.target === device.name);
  const deploy = device.last_deploy;
  const preview = device.last_removal_preview;

  // Take the focus on opening and give it back on closing (spec 011 FR-044).
  useEffect(() => {
    const opener = document.activeElement;
    close.current?.focus();
    return () => {
      if (opener instanceof HTMLElement || opener instanceof SVGElement) opener.focus();
    };
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <aside role="dialog" aria-label={device.name} className="rounded-lg border border-panel-line bg-surface p-6 text-body shadow-panel">
      <header className="flex items-start gap-4">
        <svg width="36" height="36" viewBox="-22 -22 44 44" aria-hidden="true" className="shrink-0">
          <StateMark encoding={encoding} r={18} />
        </svg>
        <div className="min-w-0">
          <p className="font-mono text-title font-semibold">{device.name}</p>
          <p className="text-muted">
            <span>{device.role ?? "role unknown"}</span> · <span className="text-ink">{encoding.label}</span>
          </p>
        </div>
        <button
          ref={close}
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="chip ml-auto rounded border border-line px-2 py-0.5 text-muted hover:text-ink"
        >
          ✕
        </button>
      </header>

      {device.health && <Health health={device.health} now={now} />}

      {!device.in_intent && <p className="mt-4 text-muted">No longer in intent.</p>}
      {device.error && (
        <p className="mt-4 max-w-prose rounded border border-warn-line bg-warn-surface p-2 break-words">{device.error}</p>
      )}

      <Paths title="In intent, missing from the device" paths={device.missing_paths ?? []} />
      <Paths title="On the device, not in intent" paths={device.undesired_paths ?? []} />
      <Paths title="Differs from intent (direction not recorded)" paths={device.unclassified_paths ?? []} />

      {device.note && <p className="mt-4 max-w-prose text-muted">{device.note}</p>}
      {device.suggested_command && (
        <p className="mt-4">
          <Command text={device.suggested_command} copy={copy} />
        </p>
      )}
      {device.remove_command && (
        <p className="mt-4">
          <Command text={device.remove_command} copy={copy} />
        </p>
      )}

      <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-4 gap-y-2">
        <dt className="text-muted">Last scan</dt>
        <dd>{ageText(device.last_scanned_at, now)}</dd>
        <dt className="text-muted">Last deploy</dt>
        <dd>
          {deploy
            ? `${deploy.success ? "Succeeded" : `Failed: ${deploy.reason ?? "no reason recorded"}`} · ${ageText(deploy.ended_at, now)}`
            : "No deploy recorded"}
        </dd>
        <dt className="text-muted">Last removal preview</dt>
        <dd>{preview ? `${previewCounts(preview)} · ${ageText(preview.at, now)}` : "None recorded"}</dd>
      </dl>
      {/* What the latest preview left in place, so config kept by design reads apart from config nobody looked at (spec 013). */}
      <Paths title="Left in place by the last preview" paths={preview?.refused_paths ?? []} />

      <section className="mt-4">
        <h3 className="text-meta font-semibold text-muted">Recent runs</h3>
        {own.length === 0 ? (
          <p className="mt-2 text-muted">No runs recorded for this device.</p>
        ) : (
          <ul aria-label={`Recent runs for ${device.name}`} className="mt-2 space-y-2">
            {own.map((run) => {
              const outcome = runEncoding(run.state);
              return (
                <li key={`${run.workflow_id}@${run.started_at}`} className="flex flex-wrap items-baseline gap-x-2">
                  <span aria-hidden="true">{outcome.glyph}</span>
                  <span>{run.workflow_type}</span>
                  <span>{outcome.label}</span>
                  {run.reason && <span className="break-words text-muted">{run.reason}</span>}
                  <span className="ml-auto text-meta text-muted">
                    {run.trigger} · {ageText(run.started_at, now)}
                  </span>
                  {onSelectRun && (
                    <button
                      type="button"
                      aria-pressed={
                        selectedRun?.workflow_id === run.workflow_id && selectedRun.started_at === run.started_at
                      }
                      aria-label={`Show the loop for ${run.workflow_type} started ${utcText(new Date(run.started_at))}`}
                      onClick={() => onSelectRun({ workflow_id: run.workflow_id, started_at: run.started_at })}
                      className="chip rounded border border-line px-1.5 text-meta aria-pressed:border-ink aria-pressed:font-semibold"
                    >
                      Loop
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {network && <Network view={network} device={device} />}

      {linkNotes.length > 0 && (
        <section className="mt-4">
          <h3 className="text-meta font-semibold text-muted">Links in intent</h3>
          <ul className="mt-2 space-y-2 text-muted">
            {linkNotes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </section>
      )}
    </aside>
  );
}
