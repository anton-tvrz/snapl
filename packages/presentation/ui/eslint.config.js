import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";

// Text from devices and the audit trail is rendered as text only (spec 011
// FR-006). React escapes strings; this is the one way around that.
const NO_RAW_HTML = [
  {
    selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
    message: "Device-supplied text must be rendered as text (spec 011 FR-006).",
  },
  {
    selector: "Property[key.name='dangerouslySetInnerHTML']",
    message: "Device-supplied text must be rendered as text (spec 011 FR-006).",
  },
  {
    selector: "MemberExpression[property.name=/^(innerHTML|outerHTML)$/]",
    message: "Device-supplied text must be rendered as text (spec 011 FR-006).",
  },
  {
    selector: "CallExpression[callee.property.name='insertAdjacentHTML']",
    message: "Device-supplied text must be rendered as text (spec 011 FR-006).",
  },
];

export default tseslint.config(
  { ignores: ["node_modules", "coverage", "src/api/schema.d.ts", "playwright-report", "test-results"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    languageOptions: { globals: { ...globals.browser, ...globals.node } },
    rules: {
      "no-restricted-syntax": ["error", ...NO_RAW_HTML],
      // An unused disable comment is how a ban gets switched off quietly.
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
    },
    linterOptions: { reportUnusedDisableDirectives: "error" },
  },
);
