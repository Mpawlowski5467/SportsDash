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
          headers: req.headers,
          body: req.body,
        });
      } catch (error) {
        console.error(`Proxy error for ${req.method} ${pathname}: ${error}`);
        return new Response("Bad Gateway", { status: 502 });
      }
    }

    // Static assets from the build output. URL parsing has already
    // normalized any ".." segments, so this cannot escape DIST_DIR.
    const assetPath = pathname === "/" ? "/index.html" : pathname;
    const file = Bun.file(DIST_DIR + assetPath);
    if (await file.exists()) {
      return new Response(file);
    }

    // SPA fallback: extension-less paths get the app shell.
    const lastSegment = pathname.slice(pathname.lastIndexOf("/") + 1);
    if (!lastSegment.includes(".")) {
      return new Response(Bun.file(`${DIST_DIR}/index.html`));
    }

    return new Response("Not Found", { status: 404 });
  },
});

console.log(
  `SportsDash frontend listening on http://localhost:${server.port} (proxying /api -> ${API_URL})`,
);
