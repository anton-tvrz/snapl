// Fails when src/api/schema.d.ts is not what the OpenAPI snapshot generates
// (spec 011 FR-007). The snapshot is pinned to the server by a pytest, so this
// closes the chain: Pydantic models -> snapshot -> the UI's types.
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const snapshot = resolve(root, "../../../tests/unit/test_presentation/fixtures/openapi_v1.json");
const committed = join(root, "src/api/schema.d.ts");
const scratch = mkdtempSync(join(tmpdir(), "snapl-types-"));
const fresh = join(scratch, "schema.d.ts");

try {
  execFileSync(join(root, "node_modules/.bin/openapi-typescript"), [snapshot, "-o", fresh], { stdio: "pipe" });
  if (readFileSync(fresh, "utf8") !== readFileSync(committed, "utf8")) {
    console.error("src/api/schema.d.ts is out of date with the OpenAPI snapshot.");
    console.error("Regenerate it with: uv run invoke dashboard.types");
    process.exit(1);
  }
  console.log("src/api/schema.d.ts matches the OpenAPI snapshot.");
} finally {
  rmSync(scratch, { recursive: true, force: true });
}
