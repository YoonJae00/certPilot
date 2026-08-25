"""점검 ↔ 인증기준 항목 매핑 로더.

매핑은 코드가 아니라 `data/rules/aws_rules.yaml` 에 있다(PRD §9). 안내서가 개정되면
YAML 만 고치면 되도록, 여기서는 파일을 읽어 검증만 한다.
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

# apps/api/app/connectors/mapping.py -> 리포 루트
REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_RULES_PATH = REPO_ROOT / "data" / "rules" / "aws_rules.yaml"

_REQUIRED_KEYS = ("check_id", "title", "source", "criterion_codes", "pass_condition")


class MappingError(RuntimeError):
    """매핑 파일이 없거나 형식이 어긋날 때."""


@dataclass(frozen=True)
class CheckMapping:
    """점검 1개의 메타데이터."""

    check_id: str
    title: str
    source: str
    criterion_codes: tuple[str, ...]
    pass_condition: str
    # payload 키 → 한국어 라벨. 변경 감지 메시지를 만들 때 쓴다.
    metrics: tuple[tuple[str, str], ...] = ()

    @property
    def metric_labels(self) -> dict[str, str]:
        """`metrics` 를 딕셔너리로 본다."""
        return dict(self.metrics)


def _parse_entry(raw: Any) -> CheckMapping:
    """YAML 항목 1개를 `CheckMapping` 으로 옮긴다."""
    if not isinstance(raw, dict):
        raise MappingError("checks 항목은 매핑(딕셔너리)이어야 한다")

    missing = [key for key in _REQUIRED_KEYS if not raw.get(key)]
    if missing:
        raise MappingError(
            f"점검 {raw.get('check_id', '?')} 에 필수 키가 없다: {', '.join(missing)}"
        )

    codes = raw["criterion_codes"]
    if not isinstance(codes, list) or not all(isinstance(code, str) for code in codes):
        raise MappingError(f"점검 {raw['check_id']} 의 criterion_codes 는 문자열 목록이어야 한다")

    metrics_raw = raw.get("metrics") or {}
    if not isinstance(metrics_raw, dict):
        raise MappingError(f"점검 {raw['check_id']} 의 metrics 는 매핑이어야 한다")

    return CheckMapping(
        check_id=str(raw["check_id"]),
        title=str(raw["title"]),
        source=str(raw["source"]),
        criterion_codes=tuple(str(code) for code in codes),
        pass_condition=" ".join(str(raw["pass_condition"]).split()),
        metrics=tuple((str(key), str(value)) for key, value in metrics_raw.items()),
    )


@lru_cache
def load_check_mappings(path: Path | None = None) -> dict[str, CheckMapping]:
    """매핑 파일을 읽어 `check_id → CheckMapping` 으로 돌려준다(순서 유지).

    파일이 자주 바뀌지 않으므로 캐시한다. 테스트에서 갈아끼울 때는
    `load_check_mappings.cache_clear()` 를 부른다.
    """
    source_path = path or DEFAULT_RULES_PATH
    if not source_path.exists():
        raise MappingError(f"AWS 점검 매핑 파일이 없다: {source_path}")

    with source_path.open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)

    if not isinstance(payload, dict) or not isinstance(payload.get("checks"), list):
        raise MappingError(f"AWS 점검 매핑 파일 형식이 어긋난다: {source_path}")

    mappings: dict[str, CheckMapping] = {}
    for raw in payload["checks"]:
        mapping = _parse_entry(raw)
        if mapping.check_id in mappings:
            raise MappingError(f"중복된 check_id 다: {mapping.check_id}")
        mappings[mapping.check_id] = mapping

    if not mappings:
        raise MappingError(f"점검이 하나도 없다: {source_path}")
    return mappings


def get_mapping(check_id: str) -> CheckMapping | None:
    """점검 1개의 매핑. 없으면 None."""
    return load_check_mappings().get(check_id)
