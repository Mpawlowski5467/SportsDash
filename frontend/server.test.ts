import { describe, expect, it } from "vitest";

import { cacheControlFor, upstreamHeaders } from "./server";

describe("upstreamHeaders", () => {
  it("forwards only the safe allowlist, never Host or hop-by-hop headers", () => {
    const headers = new Headers({
      accept: "application/json",
      "accept-language": "en-US,en;q=0.9",
      range: "bytes=0-1023",
      // Everything below must be dropped: Host is set by fetch from the
      // target URL, and hop-by-hop headers describe the client->proxy hop,
      // not the proxy->backend one.
      host: "localhost:3000",
      connection: "keep-alive",
      "keep-alive": "timeout=5",
      "transfer-encoding": "chunked",
      "content-length": "1234",
      "accept-encoding": "gzip",
      "x-forwarded-for": "10.0.0.1",
      cookie: "session=abc",
    });

    const forwarded = upstreamHeaders(headers, "GET");

    expect(forwarded.get("accept")).toBe("application/json");
    expect(forwarded.get("accept-language")).toBe("en-US,en;q=0.9");
    expect(forwarded.get("range")).toBe("bytes=0-1023");
    for (const dropped of [
      "host",
      "connection",
      "keep-alive",
      "transfer-encoding",
      "content-length",
      "accept-encoding",
      "x-forwarded-for",
      "cookie",
    ]) {
      expect(forwarded.get(dropped)).toBeNull();
    }
  });

  it("forwards content-type only for requests that can carry a body", () => {
    const headers = new Headers({ "content-type": "application/json" });
    expect(upstreamHeaders(headers, "POST").get("content-type")).toBe(
      "application/json",
    );
    expect(upstreamHeaders(headers, "PUT").get("content-type")).toBe(
      "application/json",
    );
    expect(upstreamHeaders(headers, "GET").get("content-type")).toBeNull();
    expect(upstreamHeaders(headers, "HEAD").get("content-type")).toBeNull();
  });
});

describe("cacheControlFor", () => {
  it("marks Vite's content-hashed /assets/ bundles immutable", () => {
    expect(cacheControlFor("/assets/index-BKeNmdq_.css")).toBe(
      "public, max-age=31536000, immutable",
    );
    expect(cacheControlFor("/assets/MapView-B3VPbF-V.js")).toBe(
      "public, max-age=31536000, immutable",
    );
  });

  it("revalidates index.html and anything that can change under the same name", () => {
    expect(cacheControlFor("/index.html")).toBe("no-cache");
    expect(cacheControlFor("/manifest.webmanifest")).toBe("no-cache");
    expect(cacheControlFor("/favicon.svg")).toBe("no-cache");
  });
});
