#!/usr/bin/env bash
#
# CertPilot 데모 배포 스크립트 (리눅스 VM 1대).
#
# 특정 클라우드에 묶이지 않는다. docker + compose v2 가 있으면 AWS EC2 든
# 오라클 클라우드 무료 VM 이든 같은 명령으로 배포된다.
#
# 하는 일
#   1. 최신 소스를 받는다(--pull 일 때만).
#   2. 베이스 이미지를 당기고 api·web 이미지를 빌드한다.
#   3. alembic upgrade head 로 스키마를 올린다(일회성 컨테이너).
#   4. 서비스를 무중단에 가깝게 재기동하고 헬스체크를 기다린다.
#
# 사용법
#   cd /srv/certpilot/infra/deploy
#   ./deploy.sh                      # 이미 받아 둔 소스로 빌드·배포
#   ./deploy.sh --pull               # git pull 부터 한다
#   ./deploy.sh --seed-demo          # 배포 뒤 데모 시드까지 적재(데이터가 지워진다!)
#   ./deploy.sh --local-db           # 관리형 DB 없이 postgres 컨테이너도 띄운다
#   ./deploy.sh --local-storage      # S3 없이 MinIO 컨테이너도 띄운다
#   ./deploy.sh --profiles local-db,local-storage   # 위 둘을 한 번에
#
# 프로파일은 env 로도 고정할 수 있다(서버마다 조합이 다를 때 편하다).
#   .env.prod 안에:  CERTPILOT_PROFILES=local-db,local-storage
#   또는 셸에서:      CERTPILOT_PROFILES=local-db ./deploy.sh
# 우선순위: CLI 플래그 > 셸 환경 변수 > .env.prod. 값은 합쳐지지 않고 덮어쓴다.
#
# 전제
#   - `.env.prod` 가 이 디렉터리에 있고 값이 채워져 있다(.env.prod.example 참고).
#   - docker 와 docker compose v2 가 설치돼 있다.
#   - 실행 사용자가 docker 그룹에 속한다.
#
# ⚠️ arm64(오라클 Ampere A1, AWS Graviton) 주의
#   - 이미지는 **빌드하는 그 VM 의 아키텍처로** 만들어진다. arm64 VM 에서 빌드한
#     certpilot-api / certpilot-web 이미지는 x86_64 서버에서 돌지 않는다.
#     이미지를 옮겨 쓸 계획이면 buildx 로 멀티아치 빌드를 따로 해야 한다.
#   - 베이스 이미지(python:3.12-slim-bookworm, node:20-alpine, redis:7,
#     pgvector/pgvector:pg16, minio/minio, postgres:16-alpine)는 모두 linux/arm64
#     매니페스트가 있어야 한다. 확인 방법은 README-deploy.md §1.2.
#   - 순수 파이썬이 아닌 휠(psycopg, lxml 등)은 arm64 휠이 없으면 소스 빌드로
#     넘어가 빌드가 몇 분씩 길어진다. 실패하면 로그에서 컴파일러 오류를 먼저 본다.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.prod.yml"
ENV_FILE="${SCRIPT_DIR}/.env.prod"

KNOWN_PROFILES=("local-db" "local-storage")

DO_PULL=0
DO_SEED=0
CLI_PROFILES=""
HAS_CLI_PROFILES=0

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m경고: %s\033[0m\n' "$*" >&2; }
fail() {
  printf '\n\033[1;31m배포 실패: %s\033[0m\n' "$*" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --pull) DO_PULL=1 ;;
    --seed-demo) DO_SEED=1 ;;
    --local-db)
      CLI_PROFILES="${CLI_PROFILES},local-db"
      HAS_CLI_PROFILES=1
      ;;
    --local-storage)
      CLI_PROFILES="${CLI_PROFILES},local-storage"
      HAS_CLI_PROFILES=1
      ;;
    --profiles)
      shift
      [[ $# -gt 0 ]] || fail "--profiles 뒤에 값이 없다 (예: --profiles local-db,local-storage)"
      CLI_PROFILES="${CLI_PROFILES},$1"
      HAS_CLI_PROFILES=1
      ;;
    --profiles=*)
      CLI_PROFILES="${CLI_PROFILES},${1#--profiles=}"
      HAS_CLI_PROFILES=1
      ;;
    -h | --help)
      sed -n '2,41p' "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *)
      echo "모르는 옵션이다: $1" >&2
      exit 2
      ;;
  esac
  shift
done

# --------------------------------------------------------------------------
# 0. 사전 점검
# --------------------------------------------------------------------------
command -v docker >/dev/null 2>&1 || fail "docker 가 없다."
docker compose version >/dev/null 2>&1 || fail "docker compose v2 가 없다."
[[ -f "${ENV_FILE}" ]] || fail ".env.prod 가 없다. .env.prod.example 을 복사해 채운다."

# 비밀 파일 권한이 헐거우면 배포를 멈춘다(PRD §10).
PERMISSION="$(stat -c '%a' "${ENV_FILE}" 2>/dev/null || stat -f '%A' "${ENV_FILE}")"
if [[ "${PERMISSION}" != "600" && "${PERMISSION}" != "400" ]]; then
  fail ".env.prod 권한이 ${PERMISSION} 다. 'chmod 600 ${ENV_FILE}' 로 조인다."
fi

if grep -q 'CHANGE_ME' "${ENV_FILE}"; then
  fail ".env.prod 에 CHANGE_ME 가 남아 있다. 값을 모두 채운다."
fi

HOST_ARCH="$(uname -m)"
log "호스트 아키텍처: ${HOST_ARCH}"
if [[ "${HOST_ARCH}" == "aarch64" || "${HOST_ARCH}" == "arm64" ]]; then
  warn "arm64 VM 이다(오라클 Ampere/Graviton). 여기서 만든 이미지는 x86_64 서버에서 돌지 않는다."
  warn "베이스 이미지에 linux/arm64 매니페스트가 없으면 pull 단계에서 멈춘다 — README-deploy.md §1.2."
fi

# --------------------------------------------------------------------------
# 1. 프로파일 결정 (CLI > 셸 env > .env.prod)
# --------------------------------------------------------------------------
read_env_value() {
  # .env.prod 에서 키 하나의 값만 읽는다. 파일 전체를 source 하면 비밀 값이 이
  # 스크립트의 자식 프로세스로 전부 새기 때문에 그렇게 하지 않는다.
  # 셸 환경 변수가 이미 있으면 그쪽이 우선이다(compose 의 우선순위와 같다).
  local key="$1" raw
  if [[ -n "${!key:-}" ]]; then
    printf '%s' "${!key}"
    return 0
  fi
  raw="$(grep -E "^[[:space:]]*${key}[[:space:]]*=" "${ENV_FILE}" | tail -n 1 || true)"
  [[ -z "${raw}" ]] && return 0
  raw="${raw#*=}"
  raw="${raw%\"}"
  raw="${raw#\"}"
  raw="${raw%\'}"
  raw="${raw#\'}"
  printf '%s' "${raw}"
}

if [[ "${HAS_CLI_PROFILES}" -eq 1 ]]; then
  PROFILES_RAW="${CLI_PROFILES}"
  PROFILE_SOURCE="CLI 옵션"
elif [[ -n "${CERTPILOT_PROFILES:-}" ]]; then
  PROFILES_RAW="${CERTPILOT_PROFILES}"
  PROFILE_SOURCE="셸 환경 변수 CERTPILOT_PROFILES"
else
  PROFILES_RAW="$(read_env_value CERTPILOT_PROFILES)"
  PROFILE_SOURCE=".env.prod 의 CERTPILOT_PROFILES"
fi

ACTIVE_PROFILES=()
PROFILE_ARGS=()

has_profile() {
  local wanted="$1" current
  for current in ${ACTIVE_PROFILES[@]+"${ACTIVE_PROFILES[@]}"}; do
    [[ "${current}" == "${wanted}" ]] && return 0
  done
  return 1
}

IFS=',' read -r -a PROFILE_TOKENS <<<"${PROFILES_RAW}"
for TOKEN in ${PROFILE_TOKENS[@]+"${PROFILE_TOKENS[@]}"}; do
  # 앞뒤 공백을 턴다.
  TOKEN="${TOKEN#"${TOKEN%%[![:space:]]*}"}"
  TOKEN="${TOKEN%"${TOKEN##*[![:space:]]}"}"
  [[ -z "${TOKEN}" ]] && continue

  KNOWN=0
  for CANDIDATE in "${KNOWN_PROFILES[@]}"; do
    [[ "${TOKEN}" == "${CANDIDATE}" ]] && KNOWN=1
  done
  [[ "${KNOWN}" -eq 1 ]] \
    || fail "모르는 프로파일이다: '${TOKEN}' (${PROFILE_SOURCE}). 쓸 수 있는 값: ${KNOWN_PROFILES[*]}"

  has_profile "${TOKEN}" && continue
  ACTIVE_PROFILES+=("${TOKEN}")
  PROFILE_ARGS+=(--profile "${TOKEN}")
done

if [[ "${#ACTIVE_PROFILES[@]}" -gt 0 ]]; then
  log "활성 프로파일: ${ACTIVE_PROFILES[*]} (출처: ${PROFILE_SOURCE})"
else
  log "활성 프로파일 없음 — DB·오브젝트 스토리지는 외부(관리형)를 쓴다."
fi

# 프로파일을 켰을 때만 필요한 값들을 여기서 검사한다. compose 파일에서 `:?` 로
# 막으면 프로파일을 안 쓰는 배포에서도 렌더가 실패하기 때문이다(보간은 프로파일과
# 무관하게 먼저 일어난다).
if has_profile "local-db"; then
  PG_PASSWORD="$(read_env_value POSTGRES_PASSWORD)"
  [[ -n "${PG_PASSWORD}" ]] \
    || fail "local-db 프로파일에는 POSTGRES_PASSWORD 가 필요하다(.env.prod). 비어 있으면 postgres 가 기동을 거부한다."
  DB_URL="$(read_env_value DATABASE_URL)"
  if [[ -n "${DB_URL}" && "${DB_URL}" != *"@postgres:"* ]]; then
    warn "local-db 를 켰는데 DATABASE_URL 이 컨테이너(@postgres:5432)를 가리키지 않는다. 의도한 것인지 확인한다."
  fi
fi

if has_profile "local-storage"; then
  MINIO_USER="$(read_env_value S3_ACCESS_KEY)"
  MINIO_PASSWORD="$(read_env_value S3_SECRET_KEY)"
  # MinIO 가 루트 자격증명으로 그대로 받는 값이다. 짧으면 컨테이너가 뜨지 않는다.
  [[ "${#MINIO_USER}" -ge 3 ]] \
    || fail "local-storage 프로파일에는 S3_ACCESS_KEY 가 3자 이상이어야 한다(MinIO 루트 사용자, 지금 ${#MINIO_USER}자)."
  [[ "${#MINIO_PASSWORD}" -ge 8 ]] \
    || fail "local-storage 프로파일에는 S3_SECRET_KEY 가 8자 이상이어야 한다(MinIO 루트 비밀번호, 지금 ${#MINIO_PASSWORD}자)."
  S3_URL="$(read_env_value S3_ENDPOINT)"
  if [[ -n "${S3_URL}" && "${S3_URL}" != *"//minio:"* ]]; then
    warn "local-storage 를 켰는데 S3_ENDPOINT 가 http://minio:9000 이 아니다. 의도한 것인지 확인한다."
  fi
fi

# 호스트에 게시할 포트. 같은 서버에 다른 서비스가 있으면 .env.prod 에서 바꾼다
# (컨테이너 안쪽은 항상 8000/3000 이다). 헬스체크와 안내 문구가 이 값을 따라간다.
API_PORT="$(read_env_value API_PORT)"
API_PORT="${API_PORT:-8000}"
WEB_PORT="$(read_env_value WEB_PORT)"
WEB_PORT="${WEB_PORT:-3000}"

COMPOSE=(docker compose -f "${COMPOSE_FILE}" --env-file "${ENV_FILE}" ${PROFILE_ARGS[@]+"${PROFILE_ARGS[@]}"})

# --------------------------------------------------------------------------
# 2. 소스 갱신
# --------------------------------------------------------------------------
if [[ "${DO_PULL}" -eq 1 ]]; then
  log "소스 갱신 (git pull)"
  git -C "${REPO_ROOT}" pull --ff-only
fi

log "배포 대상 커밋"
git -C "${REPO_ROOT}" --no-pager log -1 --oneline || true

# --------------------------------------------------------------------------
# 3. 이미지 준비
# --------------------------------------------------------------------------
log "베이스 이미지 갱신 (docker compose pull)"
# 빌드해서 쓰는 이미지(api·web)는 레지스트리에 없으므로 실패해도 넘어간다.
"${COMPOSE[@]}" pull --ignore-buildable || true

log "이미지 빌드 (api · web)"
"${COMPOSE[@]}" build

# --------------------------------------------------------------------------
# 4. 스키마 마이그레이션
# --------------------------------------------------------------------------
log "의존 서비스 기동 (redis + 활성 프로파일의 컨테이너)"
"${COMPOSE[@]}" up -d redis
if has_profile "local-db"; then
  "${COMPOSE[@]}" up -d postgres
fi
if has_profile "local-storage"; then
  # api 가 첫 업로드에서 버킷을 만든다. 그 전에 MinIO 가 떠 있어야 한다.
  "${COMPOSE[@]}" up -d minio
fi

log "스키마 마이그레이션 (alembic upgrade head)"
"${COMPOSE[@]}" run --rm --no-deps api alembic upgrade head \
  || fail "마이그레이션에 실패했다. 서비스를 재기동하지 않았다."

# --------------------------------------------------------------------------
# 5. 재기동
# --------------------------------------------------------------------------
log "서비스 재기동"
"${COMPOSE[@]}" up -d --remove-orphans

log "헬스체크 대기 (최대 90초) — http://127.0.0.1:${API_PORT}/health"
DEADLINE=$((SECONDS + 90))
until curl -fsS "http://127.0.0.1:${API_PORT}/health" >/dev/null 2>&1; do
  if ((SECONDS >= DEADLINE)); then
    "${COMPOSE[@]}" logs --tail 50 api || true
    fail "api 가 90초 안에 뜨지 않았다."
  fi
  sleep 3
done
log "api 헬스체크 통과"

# --------------------------------------------------------------------------
# 6. (선택) 데모 시드
# --------------------------------------------------------------------------
if [[ "${DO_SEED}" -eq 1 ]]; then
  log "데모 시드 적재 — 기존 '데모핀테크' 데이터는 지워지고 다시 만들어진다"
  "${COMPOSE[@]}" run --rm --no-deps api python /srv/certpilot/scripts/seed_demo.py
fi

log "현재 상태"
"${COMPOSE[@]}" ps

# 포트를 안내에 넣어야 하므로 EOF 를 인용하지 않는다(본문에 $ 는 아래 두 변수뿐이다).
cat <<EOF

배포가 끝났다.

  api : http://127.0.0.1:${API_PORT}/health
  web : http://127.0.0.1:${WEB_PORT}

.env.prod 의 API_BIND/WEB_BIND 를 0.0.0.0 으로 뒀다면 공인 IP 로도 열린다
(클라우드 방화벽과 OS 방화벽에서 해당 포트를 함께 열어야 한다).

로그:   docker compose -f docker-compose.prod.yml --env-file .env.prod logs -f api
롤백:   IMAGE_TAG 를 이전 태그로 바꾸고 ./deploy.sh 를 다시 실행한다.
EOF
