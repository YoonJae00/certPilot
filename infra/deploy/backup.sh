#!/usr/bin/env bash
#
# CertPilot 데이터베이스 백업 (일 1회, S3 업로드, 30일 보존).
#
# 하는 일
#   1. pg_dump 로 논리 백업을 뜬다(커스텀 포맷 + 압축).
#   2. S3 에 올린다(SSE-S3 서버 측 암호화).
#   3. 로컬 임시 파일을 지운다.
#   4. 보존 기간(기본 30일)이 지난 S3 백업을 지운다.
#
# 사용법
#   ./backup.sh              # .env.prod 를 읽어 백업한다
#   ./backup.sh --dry-run    # 무엇을 할지 출력만 한다
#
# 전제
#   - `.env.prod` 에 DATABASE_URL, BACKUP_S3_BUCKET 이 있다.
#   - aws cli v2 가 설치돼 있고, EC2 인스턴스 프로파일이나 자격증명으로
#     백업 버킷에 PutObject·ListBucket·DeleteObject 권한을 가진다.
#   - pg_dump 는 docker 의 postgres 이미지로 실행하므로 호스트에 없어도 된다.
#
# crontab 등록은 `crontab.example` 참고.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env.prod"

DRY_RUN=0
[[ "${1-}" == "--dry-run" ]] && DRY_RUN=1

log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
fail() {
  log "백업 실패: $*" >&2
  exit 1
}

[[ -f "${ENV_FILE}" ]] || fail ".env.prod 가 없다: ${ENV_FILE}"

# .env.prod 를 읽는다. 값에 공백이 있어도 되도록 allexport 를 쓴다.
set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

: "${DATABASE_URL:?DATABASE_URL 이 .env.prod 에 없다}"
: "${BACKUP_S3_BUCKET:?BACKUP_S3_BUCKET 이 .env.prod 에 없다}"
BACKUP_S3_PREFIX="${BACKUP_S3_PREFIX:-postgres}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"

command -v aws >/dev/null 2>&1 || fail "aws cli 가 없다."
command -v docker >/dev/null 2>&1 || fail "docker 가 없다(pg_dump 실행에 쓴다)."

# SQLAlchemy 형식(postgresql+psycopg://)을 libpq 가 아는 형식으로 바꾼다.
PG_URL="${DATABASE_URL/postgresql+psycopg:\/\//postgresql://}"

STAMP="$(date -u '+%Y%m%dT%H%M%SZ')"
FILENAME="certpilot-${STAMP}.dump"
TMP_DIR="$(mktemp -d)"
LOCAL_PATH="${TMP_DIR}/${FILENAME}"
S3_URI="s3://${BACKUP_S3_BUCKET}/${BACKUP_S3_PREFIX}/${FILENAME}"

# 임시 파일에 DB 덤프가 들어 있다. 실패하든 성공하든 반드시 지운다.
cleanup() { rm -rf "${TMP_DIR}"; }
trap cleanup EXIT

if [[ "${DRY_RUN}" -eq 1 ]]; then
  log "[dry-run] pg_dump → ${LOCAL_PATH}"
  log "[dry-run] 업로드 → ${S3_URI}"
  log "[dry-run] ${BACKUP_RETENTION_DAYS}일 초과 백업 삭제"
  exit 0
fi

# --------------------------------------------------------------------------
# 1. 덤프
# --------------------------------------------------------------------------
log "pg_dump 시작 → ${FILENAME}"
# 커스텀 포맷(-Fc)은 pg_restore 로 선택 복원이 된다. -Z9 로 압축한다.
# 자격증명이 프로세스 목록에 노출되지 않도록 URL 은 환경 변수로 넘긴다.
docker run --rm \
  --network host \
  -e "PGURL=${PG_URL}" \
  -v "${TMP_DIR}:/backup" \
  postgres:16-alpine \
  sh -c 'pg_dump -Fc -Z9 --no-owner --no-privileges -d "$PGURL" -f "/backup/'"${FILENAME}"'"' \
  || fail "pg_dump 가 실패했다."

SIZE="$(du -h "${LOCAL_PATH}" | cut -f1)"
log "덤프 완료 (${SIZE})"

# --------------------------------------------------------------------------
# 2. 업로드
# --------------------------------------------------------------------------
log "S3 업로드 → ${S3_URI}"
aws s3 cp "${LOCAL_PATH}" "${S3_URI}" \
  --sse AES256 \
  --only-show-errors \
  || fail "S3 업로드가 실패했다. 로컬 덤프는 지워진다."
log "업로드 완료"

# --------------------------------------------------------------------------
# 3. 보존 기간이 지난 백업 정리
# --------------------------------------------------------------------------
log "${BACKUP_RETENTION_DAYS}일 초과 백업 정리"
CUTOFF="$(date -u -d "${BACKUP_RETENTION_DAYS} days ago" '+%Y-%m-%d' 2>/dev/null \
  || date -u -v-"${BACKUP_RETENTION_DAYS}"d '+%Y-%m-%d')"

DELETED=0
while read -r LAST_MODIFIED _ _ KEY; do
  [[ -z "${KEY-}" ]] && continue
  if [[ "${LAST_MODIFIED}" < "${CUTOFF}" ]]; then
    aws s3 rm "s3://${BACKUP_S3_BUCKET}/${KEY}" --only-show-errors
    log "삭제: ${KEY} (${LAST_MODIFIED})"
    DELETED=$((DELETED + 1))
  fi
done < <(aws s3api list-objects-v2 \
  --bucket "${BACKUP_S3_BUCKET}" \
  --prefix "${BACKUP_S3_PREFIX}/" \
  --query 'Contents[].[LastModified,Size,StorageClass,Key]' \
  --output text 2>/dev/null || true)

log "정리 완료 (삭제 ${DELETED}건, 기준일 ${CUTOFF})"
log "백업 성공: ${S3_URI}"
