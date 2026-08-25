#!/usr/bin/env bash
#
# CertPilot 데모 배포 스크립트 (EC2 1대 + RDS).
#
# 하는 일
#   1. 최신 소스를 받는다(--pull 일 때만).
#   2. 베이스 이미지를 당기고 api·web 이미지를 빌드한다.
#   3. alembic upgrade head 로 스키마를 올린다(일회성 컨테이너).
#   4. 서비스를 무중단에 가깝게 재기동하고 헬스체크를 기다린다.
#
# 사용법
#   cd /srv/certpilot/infra/deploy
#   ./deploy.sh              # 이미 받아 둔 소스로 빌드·배포
#   ./deploy.sh --pull       # git pull 부터 한다
#   ./deploy.sh --seed-demo  # 배포 뒤 데모 시드까지 적재한다(데이터가 지워진다!)
#   ./deploy.sh --local-db   # RDS 없이 postgres 컨테이너도 함께 띄운다
#
# 전제
#   - `.env.prod` 가 이 디렉터리에 있고 값이 채워져 있다(.env.prod.example 참고).
#   - docker 와 docker compose v2 가 설치돼 있다.
#   - 실행 사용자가 docker 그룹에 속한다.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.prod.yml"
ENV_FILE="${SCRIPT_DIR}/.env.prod"

DO_PULL=0
DO_SEED=0
PROFILE_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --pull) DO_PULL=1 ;;
    --seed-demo) DO_SEED=1 ;;
    --local-db) PROFILE_ARGS+=(--profile local-db) ;;
    -h | --help)
      sed -n '2,25p' "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *)
      echo "모르는 옵션이다: $1" >&2
      exit 2
      ;;
  esac
  shift
done

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
fail() {
  printf '\n\033[1;31m배포 실패: %s\033[0m\n' "$*" >&2
  exit 1
}

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

COMPOSE=(docker compose -f "${COMPOSE_FILE}" --env-file "${ENV_FILE}" "${PROFILE_ARGS[@]}")

# --------------------------------------------------------------------------
# 1. 소스 갱신
# --------------------------------------------------------------------------
if [[ "${DO_PULL}" -eq 1 ]]; then
  log "소스 갱신 (git pull)"
  git -C "${REPO_ROOT}" pull --ff-only
fi

log "배포 대상 커밋"
git -C "${REPO_ROOT}" --no-pager log -1 --oneline || true

# --------------------------------------------------------------------------
# 2. 이미지 준비
# --------------------------------------------------------------------------
log "베이스 이미지 갱신 (docker compose pull)"
# 빌드해서 쓰는 이미지(api·web)는 레지스트리에 없으므로 실패해도 넘어간다.
"${COMPOSE[@]}" pull --ignore-buildable || true

log "이미지 빌드 (api · web)"
"${COMPOSE[@]}" build

# --------------------------------------------------------------------------
# 3. 스키마 마이그레이션
# --------------------------------------------------------------------------
log "의존 서비스 기동 (redis · postgres)"
"${COMPOSE[@]}" up -d redis
if [[ " ${PROFILE_ARGS[*]-} " == *"local-db"* ]]; then
  "${COMPOSE[@]}" up -d postgres
fi

log "스키마 마이그레이션 (alembic upgrade head)"
"${COMPOSE[@]}" run --rm --no-deps api alembic upgrade head \
  || fail "마이그레이션에 실패했다. 서비스를 재기동하지 않았다."

# --------------------------------------------------------------------------
# 4. 재기동
# --------------------------------------------------------------------------
log "서비스 재기동"
"${COMPOSE[@]}" up -d --remove-orphans

log "헬스체크 대기 (최대 90초)"
DEADLINE=$((SECONDS + 90))
until curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; do
  if ((SECONDS >= DEADLINE)); then
    "${COMPOSE[@]}" logs --tail 50 api || true
    fail "api 가 90초 안에 뜨지 않았다."
  fi
  sleep 3
done
log "api 헬스체크 통과"

# --------------------------------------------------------------------------
# 5. (선택) 데모 시드
# --------------------------------------------------------------------------
if [[ "${DO_SEED}" -eq 1 ]]; then
  log "데모 시드 적재 — 기존 '데모핀테크' 데이터는 지워지고 다시 만들어진다"
  "${COMPOSE[@]}" run --rm --no-deps api python /srv/certpilot/scripts/seed_demo.py
fi

log "현재 상태"
"${COMPOSE[@]}" ps

cat <<'EOF'

배포가 끝났다.

  api : http://127.0.0.1:8000/health  (리버스 프록시 뒤에 둔다)
  web : http://127.0.0.1:3000

로그:   docker compose -f docker-compose.prod.yml --env-file .env.prod logs -f api
롤백:   IMAGE_TAG 를 이전 태그로 바꾸고 ./deploy.sh 를 다시 실행한다.
EOF
