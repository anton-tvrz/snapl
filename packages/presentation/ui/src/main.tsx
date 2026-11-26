import "./index.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { providerFor } from "./data/inline";

const root = document.getElementById("root");
if (root) {
  // A snapshot file carries its data, packed (spec 018 US5); a served page asks the server.
  providerFor(document).then(
    (provider) =>
      createRoot(root).render(
        <StrictMode>
          <App provider={provider} />
        </StrictMode>,
      ),
    (error: unknown) => {
      // A bundle this page cannot read is refused, not half shown (contracts/cli-and-build.md).
      root.textContent = error instanceof Error ? error.message : String(error);
    },
  );
}
