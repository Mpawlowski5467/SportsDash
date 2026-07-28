import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
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
