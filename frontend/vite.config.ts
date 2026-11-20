/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backendHttp = process.env.VITE_BACKEND_URL || "http://127.0.0.1:8000";
const backendWs = backendHttp.replace(/^http/, "ws");

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: backendHttp,
        changeOrigin: true,
      },
      "/ws": {
        target: backendWs,
        ws: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/testSetup.ts",
  },
});
