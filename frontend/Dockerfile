FROM oven/bun:1 AS builder

WORKDIR /app

# Wildcard so the build works with or without a committed lockfile.
COPY package.json bun.lock* ./
RUN bun install

COPY . .
RUN bun run build


FROM oven/bun:1

WORKDIR /app

COPY --from=builder /app/dist ./dist
COPY --from=builder /app/server.ts ./server.ts

EXPOSE 3000

CMD ["bun", "server.ts"]
