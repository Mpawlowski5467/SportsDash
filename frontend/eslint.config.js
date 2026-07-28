// Focused lint config: the codebase already typechecks with strict tsc, so
// linting concentrates on what tsc can't see — the rules of hooks and
// effect dependency lists (several files hand-tune deps with
// eslint-disable comments that previously referenced a rule that never ran).
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";

export default tseslint.config(
  { ignores: ["dist", "node_modules", "src-tauri", "public", "scripts"] },
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: { parser: tseslint.parser },
    plugins: { "react-hooks": reactHooks },
    rules: {
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
    },
  },
  // The Bun server ships in the Docker image and serves every request, so it
  // is linted too — but it holds no React, which is all the block above knows
  // how to check. It gets typescript-eslint's recommended set instead: the
  // generic TypeScript smells (an unused binding, `any` widening its way into
  // a request path, a non-null assertion standing in for a real guard) that
  // strict tsc accepts. Its vitest suite is included so a rule can't be
  // dodged by moving code into the test.
  {
    files: ["server.ts", "server.test.ts"],
    extends: [tseslint.configs.recommended],
    languageOptions: { parser: tseslint.parser },
  },
);
