import { defineConfig, mergeConfig } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";

import base from "./vite.config.ts";

// The snapshot template (spec 011 research R14): one HTML file with every
// script, style and font inlined, written next to the served UI. `snapl
// dashboard --snapshot` fills its snapl-bundle element.
export default mergeConfig(
  base,
  defineConfig({
    plugins: [viteSingleFile()],
    build: {
      // The served UI was just built into the same directory.
      emptyOutDir: false,
      rollupOptions: { input: "snapshot.html" },
    },
  }),
);
