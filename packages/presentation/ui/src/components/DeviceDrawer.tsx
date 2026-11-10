import { useEffect, useRef } from "react";

import type { DeviceView, RunView } from "../api/types";
import { driftEncoding, runEncoding } from "../lib/encoding";
import { ageText } from "../lib/time";
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
}

function Paths({ title, paths }: { title: string; paths: readonly string[] }) {
  if (paths.length === 0) return null;
  return (
    <section aria-label={title} className="mt-4">
      <h3 className="text-xs font-semibold text-muted">
        {title} ({paths.length})
      </h3>
      <ul className="mt-1 space-y-1">
        {paths.map((path) => (
          <li key={path} className="rounded bg-sunken px-2 py-1 font-mono text-xs break-all">
            {path}
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * One device's detail (spec 011 FR-016): how it differs from intent, when it
 * was last scanned and deployed, its recent runs, and the command that would
 * reconcile it. Everything recorded from the device is rendered as text.
 */
export function DeviceDrawer({ device, runs, linkNotes, now, onClose, copy = true }: Props) {
  const encoding = driftEncoding(device.drift_state);
  const close = useRef<HTMLButtonElement>(null);
  const own = runs.filter((run) => run.target === device.name);
  const deploy = device.last_deploy;

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
    <aside role="dialog" aria-label={device.name} className="rounded-lg border border-line bg-surface p-4 text-sm">
      <header className="flex items-start gap-3">
        <svg width="36" height="36" viewBox="-22 -22 44 44" aria-hidden="true" className="shrink-0">
          <StateMark encoding={encoding} r={18} />
        </svg>
        <div className="min-w-0">
          <p className="font-mono text-base font-semibold">{device.name}</p>
          <p className="text-muted">
            <span>{device.role ?? "role unknown"}</span> · <span className="text-ink">{encoding.label}</span>
          </p>
        </div>
        <button
          ref={close}
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="ml-auto rounded border border-line px-2 py-0.5 text-muted hover:text-ink"
        >
          ✕
        </button>
      </header>

      {!device.in_intent && <p className="mt-3 text-muted">No longer in intent.</p>}
      {device.error && (
        <p className="mt-3 rounded border border-warn-line bg-warn-surface p-2 break-words">{device.error}</p>
      )}

      <Paths title="In intent, missing from the device" paths={device.missing_paths ?? []} />
      <Paths title="On the device, not in intent" paths={device.undesired_paths ?? []} />
      <Paths title="Differs from intent (direction not recorded)" paths={device.unclassified_paths ?? []} />

      {device.note && <p className="mt-3 text-muted">{device.note}</p>}
      {device.suggested_command && (
        <p className="mt-3">
          <Command text={device.suggested_command} copy={copy} />
        </p>
      )}

      <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
        <dt className="text-muted">Last scan</dt>
        <dd>{ageText(device.last_scanned_at, now)}</dd>
        <dt className="text-muted">Last deploy</dt>
        <dd>
          {deploy
            ? `${deploy.success ? "Succeeded" : `Failed: ${deploy.reason ?? "no reason recorded"}`} · ${ageText(deploy.ended_at, now)}`
            : "No deploy recorded"}
        </dd>
      </dl>

      <section className="mt-4">
        <h3 className="text-xs font-semibold text-muted">Recent runs</h3>
        {own.length === 0 ? (
          <p className="mt-1 text-muted">No runs recorded for this device.</p>
        ) : (
          <ul aria-label={`Recent runs for ${device.name}`} className="mt-1 space-y-1">
            {own.map((run) => {
              const outcome = runEncoding(run.state);
              return (
                <li key={`${run.workflow_id}@${run.started_at}`} className="flex flex-wrap items-baseline gap-x-2">
                  <span aria-hidden="true">{outcome.glyph}</span>
                  <span>{run.workflow_type}</span>
                  <span>{outcome.label}</span>
                  {run.reason && <span className="break-words text-muted">{run.reason}</span>}
                  <span className="ml-auto text-xs text-muted">
                    {run.trigger} · {ageText(run.started_at, now)}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {linkNotes.length > 0 && (
        <section className="mt-4">
          <h3 className="text-xs font-semibold text-muted">Links in intent</h3>
          <ul className="mt-1 space-y-1 text-muted">
            {linkNotes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </section>
      )}
    </aside>
  );
}
