/// <reference types="vitest/config" />
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 배포할 때 빌드한 dist/를 BE가 그대로 내보낸다(ADR 0006). dist/는 커밋하지 않는다.
// base를 "./"로 둬서 어느 경로에 올려도 파일을 찾게 한다.
// 페이지는 두 장이다: 결과 `index.html`, 업로드 `upload/index.html`(이슈 #74).
export default defineConfig({
  base: "./",
  plugins: [react()],
  build: {
    rollupOptions: {
      input: {
        result: fileURLToPath(new URL("./index.html", import.meta.url)),
        upload: fileURLToPath(new URL("./upload/index.html", import.meta.url)),
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
  },
});
