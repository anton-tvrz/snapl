// What is selected lives in the page address, so a link reproduces the view
// (spec 011 FR-032). It is written with replaceState: scrubbing through time
// must not fill the back button's history.
import type { RunId } from "../data/provider";

export interface UrlState {
  /** The selected device's name. */
  device?: string;
  /** The past time being shown. Unset means now. */
  at?: Date;
  from?: Date;
  to?: Date;
  run?: RunId;
}

function date(value: string | null): Date | undefined {
  if (!value) return undefined;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? undefined : parsed;
}

function run(value: string | null): RunId | undefined {
  // The start time is after the last "@": a workflow id may contain one.
  const split = value?.lastIndexOf("@") ?? -1;
  if (!value || split <= 0) return undefined;
  const started_at = value.slice(split + 1);
  return date(started_at) ? { workflow_id: value.slice(0, split), started_at } : undefined;
}

/** Reads the state from a query string. A value it cannot read is left out. */
export function parseUrlState(search: string): UrlState {
  const params = new URLSearchParams(search);
  const state: UrlState = {
    device: params.get("device") || undefined,
    at: date(params.get("at")),
    from: date(params.get("from")),
    to: date(params.get("to")),
    run: run(params.get("run")),
  };
  return Object.fromEntries(Object.entries(state).filter(([, value]) => value !== undefined));
}

/** The query string for a state, with its leading "?", or "" when empty. */
export function urlSearch(state: UrlState): string {
  const params = new URLSearchParams();
  if (state.device) params.set("device", state.device);
  if (state.at) params.set("at", state.at.toISOString());
  if (state.from) params.set("from", state.from.toISOString());
  if (state.to) params.set("to", state.to.toISOString());
  if (state.run) params.set("run", `${state.run.workflow_id}@${state.run.started_at}`);
  return params.size > 0 ? `?${params}` : "";
}

export function writeUrlState(state: UrlState): void {
  const search = urlSearch(state);
  if (search === location.search) return;
  history.replaceState(history.state, "", `${location.pathname}${search}${location.hash}`);
}
