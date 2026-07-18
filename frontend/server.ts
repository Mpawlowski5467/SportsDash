/**
 * Production server for the SportsDash frontend, run with `bun server.ts`.
 *
 * - Requests whose path starts with /api are proxied to the backend
 *   (API_URL, defaults to the docker-compose service name).
 * - Everything else is served from the Vite build output in ./dist.
 * - Extension-less paths fall back to dist/index.html (SPA routing).
 */

const API_URL: string = process.env.API_URL ?? "http://api:8000";
const DIST_DIR: string = `${import.meta.dir}/dist`;

// A hung upstream must fail the request, not pin it forever: without a
// timeout, one wedged backend would hold every proxied request (and its
// socket) open indefinitely.
const UPSTREAM_TIMEOUT_MS = 20_000;

// Request headers forwarded upstream. Host is deliberately absent — fetch
// sets it from the target URL — and so are the hop-by-hop headers
// (connection, content-length, accept-encoding, transfer-encoding, …):
// forwarding those verbatim either breaks the hop or lies about the body.
const FORWARDED_HEADERS = ["accept", "accept-language", "range"];

/**
 * Headers for the upstream request: a safe allowlist, never the raw
 * incoming set. Exported for tests.
 */
export function upstreamHeaders(headers: Headers, method: string): Headers {
  const forwarded = new Headers();
  for (const name of FORWARDED_HEADERS) {
    const value = headers.get(name);
    if (value !== null) forwarded.set(name, value);
  }
  // content-type only means something when a body rides along.
  if (method !== "GET" && method !== "HEAD") {
    const contentType = headers.get("content-type");
    if (contentType !== null) forwarded.set("content-type", contentType);
  }
  return forwarded;
}

/**
 * Cache policy by path: files under /assets/ are Vite's content-hashed
 * bundles (a deploy changes their names, never their contents), while
 * everything else — index.html, the manifest, icons — can change under
 * the same URL and must be revalidated. Exported for tests.
 */
export function cacheControlFor(pathname: string): string {
  return pathname.startsWith("/assets/")
    ? "public, max-age=31536000, immutable"
    : "no-cache";
}

// `import.meta.main` keeps the module importable (tests) without starting
// a listener; `bun server.ts` is always the entry point in production.
if (import.meta.main) {
  const server = Bun.serve({
    port: 3000,
    async fetch(req: Request): Promise<Response> {
      const url = new URL(req.url);
      const { pathname, search } = url;

      // Proxy API traffic to the backend, preserving the query string and
      // returning the upstream response as-is.
      if (pathname.startsWith("/api")) {
        try {
          return await fetch(API_URL + pathname + search, {
            method: req.method,
            headers: upstreamHeaders(req.headers, req.method),
            body: req.body,
            signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
          });
        } catch (error) {
          if (
            error instanceof DOMException &&
            (error.name === "TimeoutError" || error.name === "AbortError")
          ) {
            console.error(
              `Proxy timeout for ${req.method} ${pathname} after ${UPSTREAM_TIMEOUT_MS}ms`,
            );
            return new Response("Gateway Timeout", { status: 504 });
          }
          console.error(`Proxy error for ${req.method} ${pathname}: ${error}`);
          return new Response("Bad Gateway", { status: 502 });
        }
      }

      // Static assets from the build output. URL parsing has already
      // normalized any ".." segments, so this cannot escape DIST_DIR.
      const assetPath = pathname === "/" ? "/index.html" : pathname;
      const file = Bun.file(DIST_DIR + assetPath);
      if (await file.exists()) {
        return new Response(file, {
          headers: { "Cache-Control": cacheControlFor(assetPath) },
        });
      }

      // SPA fallback: extension-less paths get the app shell, which is
      // revalidated every load since it names the hashed bundles.
      const lastSegment = pathname.slice(pathname.lastIndexOf("/") + 1);
      if (!lastSegment.includes(".")) {
        return new Response(Bun.file(`${DIST_DIR}/index.html`), {
          headers: { "Cache-Control": cacheControlFor("/index.html") },
        });
      }

      return new Response("Not Found", { status: 404 });
    },
  });

  console.log(
    `SportsDash frontend listening on http://localhost:${server.port} (proxying /api -> ${API_URL})`,
  );
}
