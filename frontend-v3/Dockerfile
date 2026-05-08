# Multi-stage Next.js 16 standalone build for Compliance-Agent v3 frontend.
#
# Stage 1 builds the Next.js standalone bundle from source (npm ci against
# package-lock.json so versions match local dev exactly — TanStack Query 5,
# shadcn/ui, Inter Tight, etc.).
#
# Stage 2 ships the standalone output + static assets in a slim runtime
# image. No npm, no source, no devDependencies.

FROM node:22-alpine AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1

# Install deps with the locked versions used in dev. --include=dev so the
# build can run; production stage drops them.
COPY package.json package-lock.json ./
RUN npm ci --include=dev

# Copy the rest of the source AFTER deps so node_modules layer caches.
COPY . .

# NEXT_PUBLIC_* are baked into the bundle at build time, not runtime.
# The reviewer browser needs to know where the backend lives — pass via
# build arg defaulting to the IP-deploy URL.
ARG NEXT_PUBLIC_API_URL=
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ARG NEXT_PUBLIC_SUPABASE_URL
ENV NEXT_PUBLIC_SUPABASE_URL=$NEXT_PUBLIC_SUPABASE_URL
ARG NEXT_PUBLIC_SUPABASE_ANON_KEY
ENV NEXT_PUBLIC_SUPABASE_ANON_KEY=$NEXT_PUBLIC_SUPABASE_ANON_KEY

RUN npm run build


FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000

# Copy only what the standalone server needs.
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

EXPOSE 3000
CMD ["node", "server.js"]
