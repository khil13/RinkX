import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Relative base + HashRouter: the built site works from any path (e.g. /RinkX/ on GitHub Pages).
export default defineConfig({
  base: "./",
  plugins: [react(), tailwindcss()],
  test: {
    include: ["src/**/*.test.ts"],
    environment: "node",
  },
});
