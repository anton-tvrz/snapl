import type { MetricsViewName } from "../api/types";
import type { DataProvider, Range, RunId, RunsQuery } from "./provider";

/** A request the server refused, or one that never reached it (status 0). */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

type Params = Record<string, string | number | undefined>;

interface ValidationItem {
  loc?: unknown[];
  msg?: string;
}

function reason(status: number, body: unknown): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as ValidationItem;
    const field = first.loc?.at(-1);
    return field === undefined ? String(first.msg) : `${String(field)}: ${String(first.msg)}`;
  }
  return `the dashboard server answered ${status}`;
}

/** The dashboard's read API. It only ever sends GET requests. */
export class HttpProvider implements DataProvider {
  readonly #base: string;
  readonly #fetch: typeof fetch;

  constructor(base = "", fetchFn: typeof fetch = (...args) => fetch(...args)) {
    this.#base = base;
    this.#fetch = fetchFn;
  }

  async #get<T>(path: string, params: Params = {}): Promise<T> {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined) query.set(key, String(value));
    }
    const url = `${this.#base}/api/v1/${path}${query.size > 0 ? `?${query}` : ""}`;
    let response: Response;
    try {
      response = await this.#fetch(url, { headers: { Accept: "application/json" } });
    } catch {
      throw new ApiError(0, "the dashboard server did not answer");
    }
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      if (response.ok) throw new ApiError(response.status, "the dashboard server sent an answer that is not JSON");
    }
    if (!response.ok) throw new ApiError(response.status, reason(response.status, body));
    return body as T;
  }

  mode = () => this.#get<Awaited<ReturnType<DataProvider["mode"]>>>("mode");
  overview = () => this.#get<Awaited<ReturnType<DataProvider["overview"]>>>("overview");
  sources = () => this.#get<Awaited<ReturnType<DataProvider["sources"]>>>("sources");
  links = () => this.#get<Awaited<ReturnType<DataProvider["links"]>>>("links");
  topology = () => this.#get<Awaited<ReturnType<DataProvider["topology"]>>>("topology");

  devices = (at?: Date) =>
    this.#get<Awaited<ReturnType<DataProvider["devices"]>>>("devices", { at: at?.toISOString() });

  runs = (query: RunsQuery) =>
    this.#get<Awaited<ReturnType<DataProvider["runs"]>>>("runs", {
      at: query.at?.toISOString(),
      device: query.device,
      limit: query.limit,
    });

  history = (range?: Range, bucket?: string) =>
    this.#get<Awaited<ReturnType<DataProvider["history"]>>>("history", {
      start: range?.start.toISOString(),
      end: range?.end.toISOString(),
      bucket,
    });

  timeline = (range?: Range) =>
    this.#get<Awaited<ReturnType<DataProvider["timeline"]>>>("drift-timeline", {
      start: range?.start.toISOString(),
      end: range?.end.toISOString(),
    });

  steps = (run: RunId) =>
    this.#get<Awaited<ReturnType<DataProvider["steps"]>>>("runs/steps", {
      workflow_id: run.workflow_id,
      started_at: run.started_at,
    });

  metrics = (view: MetricsViewName, range?: Range) =>
    this.#get<Awaited<ReturnType<DataProvider["metrics"]>>>(`metrics/${view}`, {
      start: range?.start.toISOString(),
      end: range?.end.toISOString(),
    });
}
