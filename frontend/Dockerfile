FROM oven/bun:1 AS builder

WORKDIR /app

# bun.lock is committed — honor it exactly so builds are reproducible.
COPY package.json bun.lock ./
RUN bun install --frozen-lockfile

COPY . .
RUN bun run build


FROM oven/bun:1

WORKDIR /app

COPY --from=builder /app/dist ./dist
COPY --from=builder /app/server.ts ./server.ts

# The runtime only reads ./dist and proxies /api — no root needed.
USER bun

EXPOSE 3000

CMD ["bun", "server.ts"]
