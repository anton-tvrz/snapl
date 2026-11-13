import { createContext, useContext, useState } from "react";

interface Props {
  text: string;
  /** False in a recording, where the viewer cannot act on the fabric. */
  copy?: boolean;
}

/** True in a snapshot: its viewer cannot act on the fabric (contracts/cli-and-build.md). */
export const Recording = createContext(false);

/**
 * A command for the operator to run. The dashboard is read-only: this shows
 * the command and can copy it, and never runs it (spec 011 FR-004).
 */
export function Command({ text, copy = true }: Props) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const recording = useContext(Recording);
  if (recording) {
    return (
      <span className="inline-flex max-w-full flex-wrap items-center gap-2 align-middle">
        <span className="text-xs text-muted">The operator could run:</span>{" "}
        <code className="overflow-x-auto rounded bg-sunken px-1.5 py-0.5 font-mono text-[0.8125rem] whitespace-nowrap">
          {text}
        </code>
      </span>
    );
  }

  const onCopy = () => {
    navigator.clipboard.writeText(text).then(
      () => setState("copied"),
      () => setState("failed"),
    );
  };

  return (
    <span className="inline-flex max-w-full items-center gap-2 align-middle">
      <code className="select-all overflow-x-auto rounded bg-sunken px-1.5 py-0.5 font-mono text-[0.8125rem] whitespace-nowrap">
        {text}
      </code>
      {copy && (
        <button
          type="button"
          onClick={onCopy}
          aria-label={`Copy command: ${text}`}
          className="shrink-0 rounded border border-line px-1.5 py-0.5 text-xs text-muted hover:text-ink focus-visible:outline-2"
        >
          Copy
        </button>
      )}
      {state !== "idle" && (
        <span role="status" className="text-xs text-muted">
          {state === "copied" ? "Copied" : "Copy failed: select the text"}
        </span>
      )}
    </span>
  );
}
