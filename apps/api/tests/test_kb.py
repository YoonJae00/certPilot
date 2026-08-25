"""인증기준 지식베이스(criteria.json) 검증.

PRD §7 F1 의 완료 조건을 그대로 테스트로 옮긴 것이다.
criteria.json 은 `make kb` 로 생성하며 리포지토리에 커밋된 소스 오브 트루스다.
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CRITERIA_PATH = REPO_ROOT / "data" / "criteria" / "criteria.json"

CODE_PATTERN = re.compile(r"^\d\.\d{1,2}\.\d{1,2}$")
EXPECTED_TOTAL = 101
EXPECTED_PER_CHAPTER = {1: 16, 2: 64, 3: 21}
# 안내서에서 직접 확인한 항목들. 코드·명칭이 어긋나면 지식베이스가 깨진 것이다.
KNOWN_TITLES = {
    "1.1.2": "최고책임자의 지정",
    "2.5.3": "사용자 인증",
}
KNOWN_TITLE_KEYWORDS = {
    "2.7.1": "암호",
    "2.9.4": "로그",
}


# conftest.py 의 DB 준비 픽스처는 autouse 라 모든 테스트에 붙는다. 이 모듈은
# JSON 파일만 읽으므로 같은 이름의 빈 픽스처로 덮어써 DB 없이도 돌게 한다.
@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    return None


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    return None


@pytest.fixture(scope="module")
def kb() -> dict[str, Any]:
    """criteria.json 을 로드한다."""
    assert CRITERIA_PATH.exists(), f"criteria.json 이 없다. 'make kb' 를 실행할 것: {CRITERIA_PATH}"
    with CRITERIA_PATH.open(encoding="utf-8") as fp:
        data: dict[str, Any] = json.load(fp)
    return data


@pytest.fixture(scope="module")
def items(kb: dict[str, Any]) -> list[dict[str, Any]]:
    """항목 목록."""
    result: list[dict[str, Any]] = kb["items"]
    return result


def test_metadata(kb: dict[str, Any]) -> None:
    assert kb["version"] == "2023"
    assert "isms-p-guide-2023-11.pdf" in kb["source"]


def test_item_count(items: list[dict[str, Any]]) -> None:
    assert len(items) == EXPECTED_TOTAL


def test_chapter_counts(items: list[dict[str, Any]]) -> None:
    counts: dict[int, int] = {}
    for item in items:
        counts[item["chapter"]] = counts.get(item["chapter"], 0) + 1
    assert counts == EXPECTED_PER_CHAPTER


def test_codes_unique(items: list[dict[str, Any]]) -> None:
    codes = [item["code"] for item in items]
    assert len(set(codes)) == len(codes)


def test_code_format(items: list[dict[str, Any]]) -> None:
    invalid = [item["code"] for item in items if not CODE_PATTERN.match(item["code"])]
    assert invalid == []


def test_code_matches_chapter(items: list[dict[str, Any]]) -> None:
    mismatched = [
        item["code"] for item in items if int(item["code"].split(".")[0]) != item["chapter"]
    ]
    assert mismatched == []


def test_section_matches_code(items: list[dict[str, Any]]) -> None:
    mismatched = [
        item["code"]
        for item in items
        if not item["section"].startswith(".".join(item["code"].split(".")[:2]) + " ")
    ]
    assert mismatched == []


def test_requirement_length(items: list[dict[str, Any]]) -> None:
    too_short = [
        (item["code"], len(item["requirement"])) for item in items if len(item["requirement"]) <= 50
    ]
    assert too_short == []


def test_titles_present(items: list[dict[str, Any]]) -> None:
    empty = [item["code"] for item in items if not item["title"].strip()]
    assert empty == []


def test_checkpoints_present(items: list[dict[str, Any]]) -> None:
    empty = [item["code"] for item in items if len(item["checkpoints"]) < 1]
    assert empty == []


def test_list_fields_are_string_lists(items: list[dict[str, Any]]) -> None:
    for item in items:
        for field in ("checkpoints", "defect_examples", "evidence_hints"):
            values = item[field]
            assert isinstance(values, list), f"{item['code']}.{field}"
            assert all(isinstance(v, str) and v.strip() for v in values), f"{item['code']}.{field}"


def test_is_simplified_all_false(items: list[dict[str, Any]]) -> None:
    # 간편인증 목록(고시 별표)을 아직 확보하지 못해 전 항목 False 다.
    assert all(item["is_simplified"] is False for item in items)


@pytest.mark.parametrize(("code", "title"), sorted(KNOWN_TITLES.items()))
def test_known_titles(items: list[dict[str, Any]], code: str, title: str) -> None:
    found = {item["code"]: item["title"] for item in items}
    assert found[code] == title


@pytest.mark.parametrize(("code", "keyword"), sorted(KNOWN_TITLE_KEYWORDS.items()))
def test_known_title_keywords(items: list[dict[str, Any]], code: str, keyword: str) -> None:
    found = {item["code"]: item["title"] for item in items}
    assert keyword in found[code]


def test_known_sections(items: list[dict[str, Any]]) -> None:
    sections = {item["code"]: item["section"] for item in items}
    assert sections["2.5.3"] == "2.5 인증 및 권한관리"
    assert sections["1.1.2"] == "1.1 관리체계 기반 마련"
