/*
 * SportsDash service worker — minimal, network-first app-shell cache.
 *
 * Goals (kept deliberately small and safe):
 *   - Provide an offline fallback so a kiosk that briefly loses its
 *     connection still shows the app shell instead of the browser's
 *     dino page.
 *   - NEVER serve stale `/api` data: API requests are not touched by the
 *     worker at all, so live scores always come straight from the network.
 *   - Don't break `bun run dev`: the worker is only registered for the
 *     built app (see main.tsx), and even if it runs in dev it falls
 *     through to the network for everything it doesn't recognise.
 *
 * Strategy: network-first for same-origin GET navigations/assets, falling
 * back to the cache only when the network fails. Successful responses are
 * written back to the cache so the latest shell is always what we keep.
 */

const CACHE = "sportsdash-shell-v2";
const SHELL = [
  "/",
  "/index.html",
  "/manifest.webmanifest",
  "/favicon.svg",
  "/apple-touch-icon.png",
];

self.addEventListener("install", (event) => {
  // Pre-cache the shell, but don't fail the install if a file is missing.
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      .catch(() => undefined)
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  // Drop any older shell caches, then take control of open clients.
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys.filter((key) => key !== CACHE).map((key) => caches.delete(key)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;

  // Only ever handle same-origin GETs. Cross-origin requests (CDNs, the
  // backend on another host) and non-GET methods pass straight through.
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // API traffic must always hit the network and is never cached — a kiosk
  // showing yesterday's scores is worse than showing none.
  if (url.pathname.startsWith("/api")) return;

  event.respondWith(
    fetch(request)
      .then((response) => {
        // Cache successful basic responses for offline fallback. Clone
        // before the body is consumed by the caller.
        if (response && response.ok && response.type === "basic") {
          const copy = response.clone();
          caches
            .open(CACHE)
            .then((cache) => cache.put(request, copy))
            .catch(() => undefined);
        }
        return response;
      })
      .catch(async () => {
        // Offline: serve the exact match if we have it, else fall back to
        // the app shell for navigations so the SPA can still boot.
        const cached = await caches.match(request);
        if (cached) return cached;
        if (request.mode === "navigate") {
          const shell = await caches.match("/index.html");
          if (shell) return shell;
        }
        return Response.error();
      }),
  );
});
