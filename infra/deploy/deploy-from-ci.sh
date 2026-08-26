#!/usr/bin/env bash
#
# GitHub Actions CD 가 SSH 로 부르는 서버측 진입점.
#
# 흐름
#   1. CD 가 서버에서 `git fetch && git checkout --detach <sha>` 를 먼저 한다.
#   2. 이 스크립트를 `<sha>` 인자로 부른다.
#   3. `IMAGE_TAG=<sha> ./deploy.sh` 로 배포한다(태그를 커밋과 1:1 로 맞춘다).
#   4. 성공하면 `<sha>` 를 last_good 파일에 적는다.
#   5. 실패하면 last_good 커밋으로 되돌려 다시 배포하고, **종료 코드는 비0** 이다
#      (롤백에 성공해도 CD 는 빨간불이어야 한다 — 배포는 실패한 것이다).
#
# 사용법
#   infra/deploy/deploy-from-ci.sh <배포할 커밋 SHA>
#
# 전제
#   - `.env.prod` 가 이 디렉터리에 있다(체크아웃으로 지워지지 않는다 — gitignore 대상).
#   - 실행 사용자가 docker 그룹에 속하고, 리포에 쓰기 권한이 있다.
#
# 사람이 직접 배포할 때는 이 스크립트가 아니라 `./deploy.sh` 를 쓴다.

set -euo pipefail

# 이 스크립트 전체를 함수 하나로 감싼 이유: 롤백 단계에서 `git checkout` 이
# 스크립트 파일 자신을 바꿔 버린다. 함수로 미리 다 읽어 두지 않으면 bash 가
# 실행 도중 바뀐 파일을 이어서 읽어 깨진다.
main() {
  local target_sha="${1:-}"

  local script_dir repo_root last_good_file
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  repo_root="$(cd "${script_dir}/../.." && pwd)"
  # 리포 밖에 둔다. 체크아웃·클린과 무관하게 남아야 롤백 대상을 알 수 있다.
  last_good_file="${LAST_GOOD_FILE:-/opt/certpilot/last_good_sha}"

  log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
  warn() { printf '\033[1;33m경고: %s\033[0m\n' "$*" >&2; }
  err() { printf '\033[1;31m%s\033[0m\n' "$*" >&2; }

  if [[ -z "${target_sha}" ]]; then
    err "배포할 커밋 SHA 를 인자로 줘야 한다: deploy-from-ci.sh <sha>"
    return 2
  fi

  local last_good=""
  if [[ -f "${last_good_file}" ]]; then
    last_good="$(tr -d '[:space:]' <"${last_good_file}")"
  fi

  log "CD 배포 시작 — 커밋 ${target_sha}"
  if [[ -n "${last_good}" ]]; then
    printf '직전 성공 커밋: %s\n' "${last_good}"
  else
    printf '직전 성공 커밋 기록이 없다(%s). 실패해도 롤백하지 않는다.\n' "${last_good_file}"
  fi

  # IMAGE_TAG 는 셸 환경 변수가 --env-file 보다 우선한다(compose 보간 규칙).
  # 그래서 .env.prod 의 IMAGE_TAG=latest 를 손대지 않고 커밋 단위로 덮어쓸 수 있다.
  if IMAGE_TAG="${target_sha}" "${script_dir}/deploy.sh"; then
    log "배포 성공 — last_good 갱신: ${target_sha}"
    mkdir -p "$(dirname "${last_good_file}")"
    printf '%s\n' "${target_sha}" >"${last_good_file}"
    return 0
  fi

  err "배포 실패 — 커밋 ${target_sha}"

  if [[ -z "${last_good}" ]]; then
    err "롤백할 직전 성공 커밋이 없다. 서버는 실패한 상태 그대로다 — 수동 확인이 필요하다."
    return 1
  fi

  if [[ "${last_good}" == "${target_sha}" ]]; then
    err "직전 성공 커밋이 방금 실패한 커밋과 같다. 되돌릴 곳이 없다 — 수동 확인이 필요하다."
    return 1
  fi

  log "롤백 시작 — ${last_good} 로 되돌린다"
  # 이 커밋의 이미지는 이전 배포에서 이미 빌드돼 있어 docker 레이어 캐시로 금방 끝난다.
  if git -C "${repo_root}" checkout --detach "${last_good}" \
    && IMAGE_TAG="${last_good}" "${script_dir}/deploy.sh"; then
    warn "롤백 성공 — 서비스는 ${last_good} 로 복구됐다. 배포 자체는 실패이므로 CD 는 실패로 끝난다."
  else
    err "롤백도 실패했다. 서비스가 내려가 있을 수 있다 — 서버에 직접 접속해 확인한다."
  fi

  # 롤백 성공 여부와 무관하게 원 배포 실패를 CI 에 알린다.
  return 1
}

# 함수 호출과 exit 를 한 줄에 둔다(위 주석 참고 — 실행 뒤 파일을 더 읽지 않게 한다).
main "$@"; exit $?
