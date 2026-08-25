"""스냅샷 변경 감지 (PRD §7 F5).

직전 스냅샷과 이번 스냅샷을 check_id 단위로 비교해, 판정이 바뀌었거나 핵심 수치가
바뀐 점검만 골라 한국어 알림 문구를 만든다. 어떤 수치를 볼지는 코드가 아니라
`data/rules/aws_rules.yaml` 의 `metrics` 가 정한다.
"""

from dataclasses import dataclass, field
from typing import Any

from app.connectors.mapping import CheckMapping, load_check_mappings
from app.models import EvidenceStatus

# 알림 문구에 쓰는 판정 한국어 표기.
STATUS_LABELS: dict[EvidenceStatus, str] = {
    EvidenceStatus.PASS: "충족",
    EvidenceStatus.FAIL: "미충족",
    EvidenceStatus.WARN: "주의",
    EvidenceStatus.UNKNOWN: "확인 불가",
}

# 한 알림에 붙이는 수치 변화 개수 상한(문구가 길어지지 않게).
MAX_METRIC_DETAILS = 3


@dataclass(frozen=True)
class SnapshotItem:
    """스냅샷 안의 점검 결과 1건."""

    check_id: str
    status: EvidenceStatus
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DriftChange:
    """변경 감지 1건."""

    check_id: str
    message: str
    previous_status: EvidenceStatus
    current_status: EvidenceStatus


def _with_particle(word: str) -> str:
    """받침 유무에 맞춰 조사 '으로/로' 를 붙인다."""
    if not word:
        return "로"
    code = ord(word[-1])
    if 0xAC00 <= code <= 0xD7A3:
        final = (code - 0xAC00) % 28
        # 받침이 없거나(0) ㄹ(8) 이면 '로', 나머지는 '으로'.
        return f"{word}로" if final in (0, 8) else f"{word}으로"
    return f"{word}로"


def _render_metric(value: Any) -> str:
    """수치 값을 한국어 표기로 바꾼다."""
    if value is None:
        return "없음"
    if isinstance(value, bool):
        return "예" if value else "아니오"
    if isinstance(value, list | tuple | set):
        return f"{len(value)}개"
    if isinstance(value, dict):
        return f"{len(value)}건"
    return str(value)


def _metric_details(
    mapping: CheckMapping, previous: dict[str, Any], current: dict[str, Any]
) -> list[str]:
    """바뀐 수치를 `라벨 이전 → 이후` 문구 목록으로 만든다."""
    details: list[str] = []
    for key, label in mapping.metrics:
        before = _render_metric(previous.get(key))
        after = _render_metric(current.get(key))
        if before != after:
            details.append(f"{label} {before} → {after}")
    return details


def _build_message(
    mapping: CheckMapping,
    previous: SnapshotItem,
    current: SnapshotItem,
    details: list[str],
) -> str:
    """알림 문구를 만든다."""
    detail_text = ", ".join(details[:MAX_METRIC_DETAILS])
    suffix = f" ({detail_text})" if detail_text else ""

    if previous.status is not current.status:
        before = STATUS_LABELS.get(previous.status, previous.status.value)
        after = STATUS_LABELS.get(current.status, current.status.value)
        return f"[{mapping.title}] 판정이 {before} → {_with_particle(after)} 바뀌었다{suffix}"
    return f"[{mapping.title}] 점검 결과가 바뀌었다{suffix}"


def detect_drift(
    previous: dict[str, SnapshotItem],
    current: dict[str, SnapshotItem],
    *,
    mappings: dict[str, CheckMapping] | None = None,
) -> list[DriftChange]:
    """두 스냅샷을 비교해 변경 목록을 돌려준다.

    직전 스냅샷에 없던 점검(첫 수집·새로 추가된 점검)은 알리지 않는다. 비교할
    기준이 없는 것을 변화라고 부르지 않는다.
    """
    table = mappings if mappings is not None else load_check_mappings()

    changes: list[DriftChange] = []
    for check_id, item in current.items():
        before = previous.get(check_id)
        if before is None:
            continue
        mapping = table.get(check_id)
        if mapping is None:
            continue

        details = _metric_details(mapping, before.payload, item.payload)
        if before.status is item.status and not details:
            continue

        changes.append(
            DriftChange(
                check_id=check_id,
                message=_build_message(mapping, before, item, details),
                previous_status=before.status,
                current_status=item.status,
            )
        )
    return changes
