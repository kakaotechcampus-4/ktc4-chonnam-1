/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// BE가 dist/를 그대로 내보낸다(ADR 0006).
// base를 "./"로 둬서 어느 경로에 올려도 파일을 찾게 한다.
export default defineConfig({
  base: "./",
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
  },
});
