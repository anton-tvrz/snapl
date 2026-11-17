import type { RunSteps } from "../api/types";
import { blockEncoding, blockName, runEncoding, TONES } from "../lib/encoding";
import { utcText } from "../lib/time";

interface Props {
  steps: RunSteps;
}

/**
 * One run as the loop (spec 011 FR-026, US3): what each NAF block did, in loop
 * order, as far as the audit trail records it. A block is done, failed with
 * its reason, not reached, or not recorded; Presentation is where the viewer
 * is. Nothing is shown as in progress: the trail records steps that ended.
 */
export function LoopStrip({ steps }: Props) {
  const { run } = steps;
  const outcome = runEncoding(run.state);
  const started = new Date(run.started_at);

  const glyph = (status: string, dim: boolean, size: string) => {
    const encoding = blockEncoding(status);
    const tone = TONES[encoding.tone];
    return (
      <span
        data-glyph=""
        aria-hidden="true"
        className={`flex ${size} shrink-0 items-center justify-center rounded-full border border-ink text-meta font-bold`}
        style={dim ? undefined : { backgroundColor: tone.fill, color: tone.onFill }}
      >
        {encoding.glyph}
      </span>
    );
  };
  const isDim = (status: string) => status === "not_reached" || status === "not_recorded";

  return (
    <div className="flex flex-col gap-4">
      <p
        role="status"
        aria-label="The run shown"
        className="chip flex flex-wrap items-baseline gap-x-2 self-start rounded-full bg-sunken px-4 py-1 text-body"
      >
        <span className="font-medium">{run.workflow_type}</span>
        {run.target && <span className="font-mono">{run.target}</span>}
        <span aria-hidden="true">·</span>
        <span>{outcome.label}</span>
        <span aria-hidden="true">·</span>
        <span className="text-muted">started {utcText(started)}</span>
      </p>
      {!steps.recorded && (
        <p className="max-w-prose text-body text-muted">
          {run.workflow_type} steps are not recorded one by one, so only the run's outcome is known.
        </p>
      )}

      {steps.recorded ? (
        <ol
          aria-label="The loop, block by block"
          data-form="tiles"
          className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6"
        >
          {steps.blocks.map((state) => {
            const name = blockName(state.block);
            const encoding = blockEncoding(state.status);
            const dim = isDim(state.status);
            return (
              <li
                key={state.block}
                aria-label={`${name}: ${encoding.label}${state.reason ? `, ${state.reason}` : ""}`}
                className={`flex flex-col gap-2 rounded-lg border p-4 text-body ${dim ? "border-dashed border-line" : "border-line-strong"}`}
              >
                <div className="flex items-center gap-2">
                  {glyph(state.status, dim, "h-8 w-8")}
                  <span className="font-semibold">{name}</span>
                </div>
                <p>
                  {encoding.label}
                  {state.t && <span className="block text-meta text-muted">{utcText(new Date(state.t)).slice(11)}</span>}
                </p>
                {state.reason && <p className="text-meta break-words">{state.reason}</p>}
              </li>
            );
          })}
        </ol>
      ) : (
        // Nothing was recorded block by block: the six blocks on one row, not six empty boxes (spec 014 FR-031).
        <ol aria-label="The loop, block by block" data-form="compact" className="flex flex-wrap gap-2">
          {steps.blocks.map((state) => {
            const name = blockName(state.block);
            const encoding = blockEncoding(state.status);
            const dim = isDim(state.status);
            return (
              <li
                key={state.block}
                aria-label={`${name}: ${encoding.label}`}
                title={encoding.label}
                className={`chip flex items-center gap-2 rounded-full border py-1 pr-4 pl-1 text-body ${dim ? "border-dashed border-line text-muted" : "border-line-strong"}`}
              >
                {glyph(state.status, dim, "h-6 w-6")}
                <span>{name}</span>
                <span className="sr-only">{encoding.label}</span>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
