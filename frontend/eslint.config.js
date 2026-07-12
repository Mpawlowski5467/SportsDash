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
);
