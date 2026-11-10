import { DRIFT_ENCODING, driftEncoding } from "../../lib/encoding";
import { StateMark } from "./StateMark";

interface Props {
  /** The drift states of the devices drawn. Only these are listed. */
  states: readonly string[];
}

const ORDER = Object.keys(DRIFT_ENCODING);

/** What each mark means, for the states on screen (spec 011 FR-012). */
export function Legend({ states }: Props) {
  const shown = [...new Set(states)].sort((a, b) => {
    const [ia, ib] = [ORDER.indexOf(a), ORDER.indexOf(b)];
    return (ia === -1 ? ORDER.length : ia) - (ib === -1 ? ORDER.length : ib) || a.localeCompare(b);
  });

  return (
    <ul aria-label="Drift states shown" className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted">
      {shown.map((state) => {
        const encoding = driftEncoding(state);
        return (
          <li key={state} className="flex items-center gap-1.5">
            <svg width="18" height="18" viewBox="-11 -11 22 22" aria-hidden="true">
              <StateMark encoding={encoding} r={9} />
            </svg>
            <span data-label>{encoding.label}</span>
          </li>
        );
      })}
    </ul>
  );
}
