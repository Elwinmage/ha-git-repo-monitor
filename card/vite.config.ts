import { defineConfig } from "vite";

export default defineConfig({
  build: {
    outDir: "dist",
    emptyOutDir: true,
    target: "es2021",
    lib: {
      entry: "src/git-repo-monitor-card.ts",
      formats: ["es"],
      fileName: () => "git-repo-monitor-card.js",
    },
  },
});
