"""개인정보 마스킹.

PRD §7 F2: 임베딩·LLM 전송·`chunks.text` 저장 **이전에** 반드시 적용한다.
원문은 S3(로컬은 MinIO)에만 남고, DB 에는 마스킹된 텍스트만 들어간다.

정책:
- 과탐(마스킹을 덜 하는 것)보다 오탐(엉뚱한 숫자를 마스킹하는 것)이 덜 위험하지만,
  둘 다 품질 문제이므로 패턴에 경계 조건을 붙여 일반 숫자·금액·날짜는 건드리지 않는다.
- 치환 결과는 `[MASKED:rrn]` 처럼 타입을 남긴다. 어떤 종류가 지워졌는지 사람이
  판단할 수 있어야 하기 때문이다.
"""

import re
from typing import Final

# 마스킹 대상 타입. 치환 문자열은 `[MASKED:<type>]` 형식으로 통일한다.
MASK_RRN: Final = "rrn"
MASK_PHONE: Final = "phone"
MASK_EMAIL: Final = "email"
MASK_CARD: Final = "card"


def _placeholder(mask_type: str) -> str:
    """타입별 치환 문자열."""
    return f"[MASKED:{mask_type}]"


# 이메일. 로컬파트 앞뒤로 단어 문자가 붙는 경우는 제외한다.
_EMAIL_RE: Final = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*\.[A-Za-z]{2,}(?![\w.-])"
)

# 주민등록번호: 앞 6자리(생년월일) + 하이픈 + 성별코드 1~4 + 6자리.
# 성별코드 검증을 넣어 "123456-7890123" 같은 일반 숫자쌍의 오탐을 줄인다.
_RRN_RE: Final = re.compile(r"(?<![0-9])\d{6}-[1-4]\d{6}(?![0-9])")

# 카드번호: 4-4-4-4 (하이픈·공백 변형 허용, 구분자는 섞여도 받는다).
_CARD_RE: Final = re.compile(r"(?<![0-9])\d{4}[-\s]\d{4}[-\s]\d{4}[-\s]\d{4}(?![0-9])")

# 전화번호.
# - 휴대폰(01X)은 구분자가 없어도 인식한다.
# - 유선은 구분자를 필수로 둔다. 그러지 않으면 10~11자리 일반 숫자를 전부 삼킨다.
_PHONE_RE: Final = re.compile(
    r"(?<![0-9\-])(?:"
    r"01[016789][-.\s]?\d{3,4}[-.\s]?\d{4}"
    r"|0(?:2|[3-6][0-5]|70|50\d?)[-.\s]\d{3,4}[-.\s]\d{4}"
    r")(?![0-9\-])"
)

# 적용 순서가 중요하다. 이메일을 먼저 지워 로컬파트 안의 숫자열이 전화번호로
# 잡히는 것을 막고, 카드번호(4-4-4-4)를 전화번호보다 먼저 처리한다.
_RULES: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (_EMAIL_RE, MASK_EMAIL),
    (_RRN_RE, MASK_RRN),
    (_CARD_RE, MASK_CARD),
    (_PHONE_RE, MASK_PHONE),
)


def mask_text(text: str) -> str:
    """개인정보 패턴을 `[MASKED:type]` 으로 치환한 문자열을 돌려준다."""
    masked = text
    for pattern, mask_type in _RULES:
        masked = pattern.sub(_placeholder(mask_type), masked)
    return masked


def count_masked(text: str) -> dict[str, int]:
    """타입별 마스킹 대상 개수를 센다(치환하지 않는다). 로그·리포트용."""
    remaining = text
    counts: dict[str, int] = {}
    for pattern, mask_type in _RULES:
        matches = pattern.findall(remaining)
        if matches:
            counts[mask_type] = len(matches)
        remaining = pattern.sub(_placeholder(mask_type), remaining)
    return counts
