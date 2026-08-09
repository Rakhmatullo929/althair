import nextVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";

export default [
  ...nextVitals,
  ...nextTypescript,
  {
    ignores: [
      ".next/**",
      "playwright-report/**",
      "test-results/**",
      "artifacts/**",
    ],
    rules: {
      "@typescript-eslint/consistent-type-imports": "warn",
    },
  },
];
