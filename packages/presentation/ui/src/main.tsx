import "./index.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { HttpProvider } from "./data/http";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <App provider={new HttpProvider()} />
    </StrictMode>,
  );
}
