import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

/** A shared bundler helper must never pull the renderer into first paint. */
function keepMapLazy(): Plugin {
  return {
    name: "keep-map-lazy",
    generateBundle(_options, bundle) {
      const visited = new Set<string>();
      const visit = (file: string) => {
        if (visited.has(file)) return;
        visited.add(file);
        const chunk = bundle[file];
        if (chunk?.type !== "chunk") return;
        if (Object.keys(chunk.modules).some(id => id.includes("/node_modules/maplibre-gl/"))) {
          this.error("MapLibre entered the initial bundle; keep the renderer behind the lazy Map view.");
        }
        chunk.imports.forEach(visit); // dynamic imports deliberately excluded
      };
      Object.values(bundle).forEach(chunk => {
        if (chunk.type === "chunk" && chunk.isEntry) visit(chunk.fileName);
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), tailwindcss(), keepMapLazy()],
  build: {
    rollupOptions: {
      output: {
        onlyExplicitManualChunks: true,
        // Keep the renderer independently cacheable: changing a map control
        // or animation should not make returning users download it again.
        // It remains a dependency of the lazy Map view, not the initial page.
        manualChunks(id) {
          if (id.includes("/node_modules/maplibre-gl/")) return "map-engine";
        },
      },
    },
  },
  server: {
    // Vite ignores PORT and would sit on 5173 forever; honouring it lets a
    // tool that assigns a free port actually get the server it asked for.
    // Plain `bun run dev` is unaffected — no PORT, so the default stands.
    port: Number(process.env.PORT) || 5173,
    proxy: {
      // Same env var the production server reads, for the same reason: 8000 is
      // a popular port and the backend does not always get it.
      "/api": process.env.API_URL ?? "http://localhost:8000",
    },
  },
});
