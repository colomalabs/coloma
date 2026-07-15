import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const proxy = {
  "/api": "http://127.0.0.1:8001",
  "/v1": "http://127.0.0.1:8001",
};

export default defineConfig({
  plugins: [react()],
  server: { proxy },
  preview: { proxy },
});
