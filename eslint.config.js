import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";
export default tseslint.config(
  { ignores: ["**/dist/**", "**/dist-types/**", "**/wire/generated.ts", "**/wire/validators.generated.js", "**/node_modules/**"] },
  ...tseslint.configs.recommended,
  { files: ["**/*.ts", "**/*.tsx"], plugins: { "react-hooks": reactHooks }, rules: {
    "@typescript-eslint/no-explicit-any": "error",
    "react-hooks/rules-of-hooks": "error", "react-hooks/exhaustive-deps": "error",
    "complexity": ["error", 10], "max-depth": ["error", 4],
    "max-lines": ["error", { max: 500, skipBlankLines: true, skipComments: true }],
  } },
);
