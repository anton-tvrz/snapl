import "./index.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { providerFor } from "./data/inline";

const root = document.getElementById("root");
if (root) {
  try {
    // A snapshot file carries its data; a served page asks the server.
    const provider = providerFor(document);
    createRoot(root).render(
      <StrictMode>
        <App provider={provider} />
      </StrictMode>,
    );
  } catch (error) {
    // A bundle this page cannot read is refused, not half shown (contracts/cli-and-build.md).
    root.textContent = error instanceof Error ? error.message : String(error);
  }
}
