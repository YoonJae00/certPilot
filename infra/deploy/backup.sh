#!/usr/bin/env bash
#
# CertPilot 데이터베이스 백업 (일 1회, 로컬 보관 + 선택적 원격 업로드).
#
# 하는 일
#   1. pg_dump 로 논리 백업을 뜬다(커스텀 포맷 + 압축) → BACKUP_DIR 에 남긴다.
#   2. BACKUP_DIR 에서 보존 기간(기본 30일)이 지난 덤프를 지운다.
#   3. **BACKUP_S3_BUCKET 이 설정돼 있을 때만** S3 호환 스토리지에 사본을 올리고,
#      원격에서도 보존 기간이 지난 백업을 지운다.
#
# 즉 aws cli 는 **선택**이다. 클라우드 스토리지가 없는 VM(오라클 무료 VM 등)에서도
# 로컬 덤프까지는 그대로 돈다.
#
# 사용법
#   ./backup.sh              # .env.prod 를 읽어 백업한다
#   ./backup.sh --dry-run    # 무엇을 할지 출력만 한다
#
# 전제
#   - `.env.prod` 에 DATABASE_URL 이 있다.
#   - `BACKUP_DIR`(기본 /var/backups/certpilot)에 쓸 수 있다. 미리 만들어 둔다:
#       sudo mkdir -p /var/backups/certpilot && sudo chown "$USER" /var/backups/certpilot
#   - pg_dump 는 docker 의 postgres 이미지로 실행하므로 호스트에 없어도 된다.
#   - (원격 사본을 쓸 때만) aws cli v2 + 버킷에 대한
#     PutObject·ListBucket·DeleteObject 권한.
#
# ⚠️ 이 스크립트는 **DB 만** 백업한다. compose 의 `local-storage` 프로파일(MinIO)을
#    쓰는 중이라면 원문 문서는 `minio_data` 볼륨에만 있다. 그 볼륨도 따로 받아 둔다:
#      docker run --rm -v certpilot_minio_data:/data -v "$PWD:/out" alpine \
#        tar czf /out/minio-$(date -u +%Y%m%dT%H%M%SZ).tgz -C /data .
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
BACKUP_DIR="${BACKUP_DIR:-/var/backups/certpilot}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"
BACKUP_S3_BUCKET="${BACKUP_S3_BUCKET:-}"
BACKUP_S3_PREFIX="${BACKUP_S3_PREFIX:-postgres}"
BACKUP_S3_ENDPOINT="${BACKUP_S3_ENDPOINT:-}"

[[ "${BACKUP_RETENTION_DAYS}" =~ ^[0-9]+$ ]] \
  || fail "BACKUP_RETENTION_DAYS 가 숫자가 아니다: ${BACKUP_RETENTION_DAYS}"

command -v docker >/dev/null 2>&1 || fail "docker 가 없다(pg_dump 실행에 쓴다)."

# --------------------------------------------------------------------------
# 원격 업로드 사용 여부 판단
#   버킷이 비어 있으면 로컬 보관만 한다(정상 동작이다).
#   버킷을 지정했는데 aws cli 가 없으면 **설정 오류**이므로 조용히 넘기지 않는다.
# --------------------------------------------------------------------------
UPLOAD=0
if [[ -n "${BACKUP_S3_BUCKET}" ]]; then
  command -v aws >/dev/null 2>&1 \
    || fail "BACKUP_S3_BUCKET 이 설정됐는데 aws cli 가 없다. 설치하거나 이 값을 비운다."
  UPLOAD=1
fi

AWS_ARGS=()
if [[ -n "${BACKUP_S3_ENDPOINT}" ]]; then
  AWS_ARGS+=(--endpoint-url "${BACKUP_S3_ENDPOINT}")
fi

# aws cli 는 리전이 없으면 아예 실행되지 않는다. AWS 가 아닌 엔드포인트를 쓸 때는
# BACKUP_S3_REGION(없으면 S3_REGION)으로 채워 준다. AWS 라면 cli 설정을 그대로 쓴다.
BACKUP_S3_REGION="${BACKUP_S3_REGION:-}"
if [[ -z "${BACKUP_S3_REGION}" && -n "${BACKUP_S3_ENDPOINT}" ]]; then
  BACKUP_S3_REGION="${S3_REGION:-}"
fi
if [[ -n "${BACKUP_S3_REGION}" ]]; then
  AWS_ARGS+=(--region "${BACKUP_S3_REGION}")
fi

# SSE-S3 는 AWS S3 의 기능이다. 커스텀 엔드포인트(오라클·MinIO)에서는 거부될 수
# 있으므로 붙이지 않는다. 그쪽은 버킷 설정에서 저장 암호화를 켠다.
SSE_ARGS=()
if [[ "${UPLOAD}" -eq 1 && -z "${BACKUP_S3_ENDPOINT}" ]]; then
  SSE_ARGS+=(--sse AES256)
fi

# SQLAlchemy 형식(postgresql+psycopg://)을 libpq 가 아는 형식으로 바꾼다.
PG_URL="${DATABASE_URL/postgresql+psycopg:\/\//postgresql://}"

STAMP="$(date -u '+%Y%m%dT%H%M%SZ')"
FILENAME="certpilot-${STAMP}.dump"
FINAL_PATH="${BACKUP_DIR}/${FILENAME}"
S3_URI="s3://${BACKUP_S3_BUCKET}/${BACKUP_S3_PREFIX}/${FILENAME}"

if [[ "${DRY_RUN}" -eq 1 ]]; then
  log "[dry-run] 백업 디렉터리 준비 → ${BACKUP_DIR}"
  log "[dry-run] pg_dump → ${FINAL_PATH}"
  if [[ "${UPLOAD}" -eq 1 ]]; then
    log "[dry-run] 업로드 → ${S3_URI}${BACKUP_S3_ENDPOINT:+ (엔드포인트 ${BACKUP_S3_ENDPOINT})}"
    log "[dry-run] 원격에서 ${BACKUP_RETENTION_DAYS}일 초과 백업 삭제"
  else
    log "[dry-run] BACKUP_S3_BUCKET 이 비어 있다 → 원격 업로드는 건너뛴다"
  fi
  log "[dry-run] ${BACKUP_DIR} 에서 ${BACKUP_RETENTION_DAYS}일 초과 덤프 삭제"
  exit 0
fi

mkdir -p "${BACKUP_DIR}" || fail "백업 디렉터리를 만들 수 없다: ${BACKUP_DIR}"
[[ -w "${BACKUP_DIR}" ]] || fail "백업 디렉터리에 쓸 수 없다: ${BACKUP_DIR}"

# --------------------------------------------------------------------------
# 1. 덤프
# --------------------------------------------------------------------------
log "pg_dump 시작 → ${FINAL_PATH}"

# 덤프 중인 파일을 완성본으로 착각하지 않도록 임시 디렉터리에서 만들고 옮긴다.
TMP_DIR="$(mktemp -d)"
# 임시 파일에 DB 덤프가 들어 있다. 실패하든 성공하든 반드시 지운다.
cleanup() { rm -rf "${TMP_DIR}"; }
trap cleanup EXIT

# 커스텀 포맷(-Fc)은 pg_restore 로 선택 복원이 된다. -Z9 로 압축한다.
# 자격증명이 프로세스 목록에 노출되지 않도록 URL 은 환경 변수로 넘긴다.
docker run --rm \
  --network host \
  -e "PGURL=${PG_URL}" \
  -v "${TMP_DIR}:/backup" \
  postgres:16-alpine \
  sh -c 'pg_dump -Fc -Z9 --no-owner --no-privileges -d "$PGURL" -f "/backup/'"${FILENAME}"'"' \
  || fail "pg_dump 가 실패했다."

mv "${TMP_DIR}/${FILENAME}" "${FINAL_PATH}" || fail "덤프를 ${BACKUP_DIR} 로 옮기지 못했다."
chmod 600 "${FINAL_PATH}"

SIZE="$(du -h "${FINAL_PATH}" | cut -f1)"
log "덤프 완료 (${SIZE}) → ${FINAL_PATH}"

# --------------------------------------------------------------------------
# 2. 로컬 보존 정리
# --------------------------------------------------------------------------
log "로컬 ${BACKUP_RETENTION_DAYS}일 초과 덤프 정리 (${BACKUP_DIR})"
# -mtime +N 은 "수정된 지 N일을 꽉 채워 넘긴" 파일이다(경계에서 하루 여유가 생긴다).
LOCAL_DELETED=0
while IFS= read -r OLD_FILE; do
  [[ -z "${OLD_FILE}" ]] && continue
  rm -f "${OLD_FILE}"
  log "삭제: ${OLD_FILE}"
  LOCAL_DELETED=$((LOCAL_DELETED + 1))
done < <(find "${BACKUP_DIR}" -maxdepth 1 -type f -name 'certpilot-*.dump' \
  -mtime "+${BACKUP_RETENTION_DAYS}" -print)
log "로컬 정리 완료 (삭제 ${LOCAL_DELETED}건)"

if [[ "${UPLOAD}" -eq 0 ]]; then
  log "BACKUP_S3_BUCKET 이 비어 있다 — 원격 업로드는 건너뛴다."
  log "백업 성공: ${FINAL_PATH}"
  exit 0
fi

# --------------------------------------------------------------------------
# 3. 업로드 (BACKUP_S3_BUCKET 이 있을 때만)
# --------------------------------------------------------------------------
log "원격 업로드 → ${S3_URI}"
aws "${AWS_ARGS[@]}" s3 cp "${FINAL_PATH}" "${S3_URI}" \
  "${SSE_ARGS[@]}" \
  --only-show-errors \
  || fail "업로드가 실패했다. 로컬 덤프는 ${FINAL_PATH} 에 남아 있다."
log "업로드 완료"

# --------------------------------------------------------------------------
# 4. 원격 보존 정리
# --------------------------------------------------------------------------
log "원격 ${BACKUP_RETENTION_DAYS}일 초과 백업 정리"
CUTOFF="$(date -u -d "${BACKUP_RETENTION_DAYS} days ago" '+%Y-%m-%d' 2>/dev/null \
  || date -u -v-"${BACKUP_RETENTION_DAYS}"d '+%Y-%m-%d')"

DELETED=0
while read -r LAST_MODIFIED _ _ KEY; do
  [[ -z "${KEY-}" ]] && continue
  if [[ "${LAST_MODIFIED}" < "${CUTOFF}" ]]; then
    aws "${AWS_ARGS[@]}" s3 rm "s3://${BACKUP_S3_BUCKET}/${KEY}" --only-show-errors
    log "삭제: ${KEY} (${LAST_MODIFIED})"
    DELETED=$((DELETED + 1))
  fi
done < <(aws "${AWS_ARGS[@]}" s3api list-objects-v2 \
  --bucket "${BACKUP_S3_BUCKET}" \
  --prefix "${BACKUP_S3_PREFIX}/" \
  --query 'Contents[].[LastModified,Size,StorageClass,Key]' \
  --output text 2>/dev/null || true)

log "원격 정리 완료 (삭제 ${DELETED}건, 기준일 ${CUTOFF})"
log "백업 성공: ${FINAL_PATH} → ${S3_URI}"
