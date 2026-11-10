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

  return (
    <div>
      <p className="mb-3 flex flex-wrap items-baseline gap-x-2 text-sm">
        <span className="font-medium">{run.workflow_type}</span>
        {run.target && <span className="font-mono">{run.target}</span>}
        <span aria-hidden="true">·</span>
        <span>{outcome.label}</span>
        <span aria-hidden="true">·</span>
        <span className="text-muted">started {utcText(started)}</span>
      </p>
      {!steps.recorded && (
        <p className="mb-3 text-sm text-muted">
          {run.workflow_type} steps are not recorded one by one, so only the run's outcome is known.
        </p>
      )}

      <ol aria-label="The loop, block by block" className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">
        {steps.blocks.map((state) => {
          const name = blockName(state.block);
          const encoding = blockEncoding(state.status);
          const tone = TONES[encoding.tone];
          const dim = state.status === "not_reached" || state.status === "not_recorded";
          return (
            <li
              key={state.block}
              aria-label={`${name}: ${encoding.label}${state.reason ? `, ${state.reason}` : ""}`}
              className={`rounded-lg border p-3 text-sm ${dim ? "border-dashed border-line" : "border-line-strong"}`}
            >
              <div className="flex items-center gap-2">
                <span
                  data-glyph=""
                  aria-hidden="true"
                  className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-ink text-xs font-bold"
                  style={dim ? undefined : { backgroundColor: tone.fill, color: tone.onFill }}
                >
                  {encoding.glyph}
                </span>
                <span className="font-semibold">{name}</span>
              </div>
              <p className="mt-1">{encoding.label}</p>
              {state.t && <p className="text-xs text-muted">{utcText(new Date(state.t)).slice(11)}</p>}
              {state.reason && <p className="mt-1 text-xs break-words">{state.reason}</p>}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
