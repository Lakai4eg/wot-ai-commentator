import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "path";

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      input: {
        overlay: resolve(__dirname, "overlay.html"),
        panel: resolve(__dirname, "panel.html"),
      },
    },
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8710",
      "/ws": { target: "ws://127.0.0.1:8710", ws: true },
    },
  },
});
