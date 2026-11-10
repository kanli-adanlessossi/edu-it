# syntax=docker/dockerfile:1
#
# Web_App image (apps/web): Next.js standalone output on a distroless Node
# runtime. The final stage has no shell and no package manager and runs as the
# numeric non-root user 65532 (kubernetes-deployment Req 12.3, 12.4).
#
# Build from the repository root (the build reads
# packages/domain/edu_it_domain/config.py and apps/web/openapi/):
#   docker build -f deploy/images/web/Dockerfile -t edu-it-web .
#
# Base images are overridable so CI can pass digest-pinned references.

ARG BUILDER_IMAGE=node:24-slim
ARG RUNTIME_IMAGE=gcr.io/distroless/nodejs24-debian13:nonroot
# Optional absolute API base URL, inlined by Next.js at build time. Empty (the default) keeps the
# deployed image environment-neutral: the browser uses same-origin /v1 behind the ingress. Only the
# local Docker Compose stack sets it (deploy/compose/docker-compose.yml).
ARG NEXT_PUBLIC_API_BASE_URL=

# ---------------------------------------------------------------- deps
FROM ${BUILDER_IMAGE} AS deps
WORKDIR /repo/apps/web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund

# ------------------------------------------------------------- builder
FROM ${BUILDER_IMAGE} AS builder
ARG NEXT_PUBLIC_API_BASE_URL
ENV NEXT_TELEMETRY_DISABLED=1 \
    NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}
WORKDIR /repo
# The build reads the lab numbers from the single Config source.
COPY packages/domain/edu_it_domain/config.py packages/domain/edu_it_domain/config.py
WORKDIR /repo/apps/web
COPY --from=deps /repo/apps/web/node_modules ./node_modules
COPY apps/web/ ./
# build-client.mjs falls back to the published openapi/v1.json when the API
# toolchain (uv) is absent, which is the case in this stage.
RUN npm run build

# --------------------------------------------------------------- final
FROM ${RUNTIME_IMAGE} AS final
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
WORKDIR /app
COPY --from=builder --chown=65532:65532 /repo/apps/web/.next/standalone ./
COPY --from=builder --chown=65532:65532 /repo/apps/web/.next/static ./.next/static
COPY --from=builder --chown=65532:65532 /repo/apps/web/public ./public

# Numeric non-zero user: lets the kubelet verify runAsNonRoot without
# resolving a user name.
USER 65532:65532
EXPOSE 3000
# The distroless Node image's entrypoint is `node`.
CMD ["server.js"]
