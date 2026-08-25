# CertPilot 웹 이미지 (Next.js 14 App Router).
#
# 빌드 컨텍스트는 `apps/web` 이다.
#
# 빌드:
#   docker build -f apps/web/Dockerfile \
#     --build-arg NEXT_PUBLIC_API_URL=https://api.example.com apps/web
#
# `NEXT_PUBLIC_*` 는 **빌드 시점에 번들에 박힌다.** 런타임 환경 변수로는 바뀌지
# 않으므로, API 주소가 바뀌면 이미지를 다시 빌드해야 한다.

# --------------------------------------------------------------------------
# 1단계: 의존성
# --------------------------------------------------------------------------
FROM node:20-alpine AS deps

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# --------------------------------------------------------------------------
# 2단계: 빌드
# --------------------------------------------------------------------------
FROM node:20-alpine AS builder

WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

# 브라우저 번들에 들어가는 API 주소.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV NEXT_TELEMETRY_DISABLED=1

RUN npm run build

# --------------------------------------------------------------------------
# 3단계: 런타임
# --------------------------------------------------------------------------
FROM node:20-alpine AS runtime

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000

WORKDIR /app

# 루트로 돌리지 않는다(PRD §10). node 이미지의 기본 비루트 계정을 쓴다.
RUN chown node:node /app
USER node

# `next start` 로 띄우므로 운영 의존성만 남긴다(standalone 출력은 쓰지 않는다 —
# next.config.mjs 를 건드리지 않기 위해서다).
COPY --chown=node:node package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force

COPY --chown=node:node --from=builder /app/.next ./.next
COPY --chown=node:node next.config.mjs ./next.config.mjs

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD wget -qO- http://localhost:3000/ >/dev/null || exit 1

CMD ["npm", "run", "start"]
