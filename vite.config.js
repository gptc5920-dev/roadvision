import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ command }) => ({
  root: "frontend",
  base: command === "build" ? "/static/console/react/" : "/",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/media": "http://127.0.0.1:8000",
      "/_authenticated": "http://127.0.0.1:8000",
    },
  },
  test: {
    environment: "node",
  },
  build: {
    outDir: "../static/console/react",
    emptyOutDir: true,
    sourcemap: false,
  },
}));
