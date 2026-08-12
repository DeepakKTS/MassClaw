# Keep this in step with NODE_VERSION in .github/workflows/ci.yml. Node 20
# went end-of-life in April 2026.
FROM node:22-alpine AS builder

WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

RUN adduser --disabled-password --gecos '' --no-create-home appuser
USER appuser

EXPOSE 3000

HEALTHCHECK --interval=10s --timeout=5s --retries=3 CMD wget -qO- http://localhost:3000/ || exit 1

CMD ["node", "server.js"]
