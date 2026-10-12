import { StrictMode } from "react";
import { startFreshnessKeeper } from "./lib/freshness";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { applyAppearance, getAppearance } from "./lib/theme";
import "./index.css";

// Re-apply the persisted appearance through the canonical applier. The inline
// script in index.html already set the attributes to avoid a flash; this is
// the authoritative pass and covers the dev server, where the inline script
// and the bundle race.
applyAppearance(getAppearance());

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});
startFreshnessKeeper(queryClient);

const rootElement = document.getElementById("root");
if (rootElement === null) {
  throw new Error("SportsDash: #root element not found in index.html");
}

createRoot(rootElement).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);

// Register the network-first service worker for offline app-shell support.
// Only in the production build: the dev server has no /sw.js and Vite's HMR
// must not be intercepted. Failures are swallowed — the SW is pure
// enhancement and must never block boot. Skipped in the Tauri desktop build
// (VITE_TAURI): the webview loads assets from the bundle and talks to the
// local sidecar, so a cross-origin caching SW would only get in the way.
if (
  import.meta.env.PROD &&
  !import.meta.env.VITE_TAURI &&
  "serviceWorker" in navigator
) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => undefined);
  });
}
