import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";
import prettier from "eslint-config-prettier";
import { defineConfig, globalIgnores } from "eslint/config";

export default defineConfig([
  globalIgnores(["dist", "node_modules"]),
  {
    files: ["**/*.{ts,tsx}"],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
      // 모양(줄바꿈·따옴표)은 Prettier가 정한다. 겹치는 ESLint 규칙을 끈다.
      prettier,
    ],
    languageOptions: {
      globals: globals.browser,
    },
    rules: {
      // 원문은 React가 이스케이프하게 둔다. 사기 문자 원문을 HTML로 넣지 않는다(ADR 0006).
      "no-restricted-syntax": [
        "error",
        {
          selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
          message: "문자 원문을 HTML로 넣지 않는다. 글자로만 보여 준다.",
        },
      ],
    },
  },
]);
