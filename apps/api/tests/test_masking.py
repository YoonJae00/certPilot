"""개인정보 마스킹 테스트 (PRD §7 F2 AC: 8개 이상).

여기 나오는 값은 전부 형식만 맞춘 가짜 예시다. 실제 개인정보를 넣지 않는다.
"""

import pytest

from app.services.masking import count_masked, mask_text


# 이 모듈은 DB 를 쓰지 않는다. conftest 의 autouse DB 픽스처를 빈 것으로 덮어쓴다.
@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    return None


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    return None


def test_masks_resident_registration_number():
    """주민등록번호(6-7)를 마스킹한다."""
    masked = mask_text("신청인 주민등록번호는 900101-1234567 입니다.")
    assert masked == "신청인 주민등록번호는 [MASKED:rrn] 입니다."


@pytest.mark.parametrize(
    "value",
    ["800101-1234567", "991231-2345678", "010203-3456789", "451231-4567890"],
)
def test_masks_all_rrn_gender_codes(value):
    """성별코드 1~4 를 모두 인식한다(내외국인·세기 구분)."""
    assert mask_text(f"주민번호 {value} 확인") == "주민번호 [MASKED:rrn] 확인"


def test_does_not_mask_invalid_rrn_gender_code():
    """뒷자리 첫 글자가 5~9 면 주민등록번호로 보지 않는다(오탐 방지)."""
    text = "관리번호 123456-7890123 은 자산 일련번호다."
    assert mask_text(text) == text


@pytest.mark.parametrize(
    "value",
    ["010-1234-5678", "01012345678", "010 1234 5678", "016-123-4567"],
)
def test_masks_mobile_phone_variants(value):
    """휴대폰 번호는 하이픈·공백·구분자 없음 모두 인식한다."""
    assert mask_text(f"연락처 {value} 로 연락") == "연락처 [MASKED:phone] 로 연락"


@pytest.mark.parametrize("value", ["02-1234-5678", "031-123-4567", "070-1234-5678"])
def test_masks_landline_phone(value):
    """지역번호·인터넷 전화도 마스킹한다."""
    assert mask_text(f"대표전화 {value}") == "대표전화 [MASKED:phone]"


def test_masks_email():
    """이메일 주소를 마스킹한다."""
    masked = mask_text("담당자 이메일 privacy@demofintech.example 로 문의")
    assert masked == "담당자 이메일 [MASKED:email] 로 문의"


@pytest.mark.parametrize(
    "value",
    [
        "1234-5678-9012-3456",
        "1234 5678 9012 3456",
        "1234-5678 9012-3456",
    ],
)
def test_masks_card_number_variants(value):
    """카드번호는 하이픈·공백 변형을 모두 인식한다."""
    assert mask_text(f"결제수단 {value} 등록") == "결제수단 [MASKED:card] 등록"


def test_does_not_mask_plain_numbers():
    """일반 숫자·금액·날짜·버전은 건드리지 않는다(오탐 방지)."""
    text = (
        "2024년 3분기 매출은 1,234,567원이며 계약 건수는 218건이다. "
        "문서번호 SEC-POL-001, 버전 2.1, 점검 기준일 2024-09-30, 포트 8000."
    )
    assert mask_text(text) == text


def test_does_not_mask_criterion_codes():
    """인증기준 코드(2.5.3 같은 값)는 마스킹 대상이 아니다."""
    text = "항목 2.5.3 사용자 인증과 2.9.4 로그 및 접속기록 관리를 확인한다."
    assert mask_text(text) == text


def test_masks_mixed_sentence():
    """한 문장에 여러 유형이 섞여 있어도 모두 치환한다."""
    text = (
        "고객 홍길동(900101-1234567)의 연락처는 010-1234-5678, "
        "이메일은 hong@example.com, 카드번호는 1234-5678-9012-3456 이다."
    )
    masked = mask_text(text)
    assert "[MASKED:rrn]" in masked
    assert "[MASKED:phone]" in masked
    assert "[MASKED:email]" in masked
    assert "[MASKED:card]" in masked
    # 원본 값이 한 조각도 남지 않아야 한다.
    for secret in ("900101-1234567", "010-1234-5678", "hong@example.com", "1234-5678-9012-3456"):
        assert secret not in masked


def test_masking_is_idempotent():
    """이미 마스킹된 텍스트를 다시 마스킹해도 변하지 않는다."""
    once = mask_text("연락처 010-1234-5678 / 이메일 a@b.example")
    assert mask_text(once) == once


def test_count_masked_reports_types():
    """`count_masked` 는 타입별 개수를 센다(치환하지 않는다)."""
    text = "010-1234-5678, 02-1234-5678, a@b.example, 900101-1234567"
    assert count_masked(text) == {"phone": 2, "email": 1, "rrn": 1}
