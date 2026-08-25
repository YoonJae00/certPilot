"""준비도 계산과 항목 코드 정렬.

모의심사 파이프라인·API·갭 리포트가 같은 산식을 쓴다. 산식이 한 곳에만 있어야
"리포트 수치와 DB 집계가 일치한다"는 테스트가 의미를 가진다.
"""


def readiness_of(*, met: int, partial: int, unknown: int, total: int) -> float:
    """준비도(0~1). PRD §7 F8 산식.

    (met + 0.5·partial) / (전체 − unknown). unknown 은 분모에서 뺀다 — 모르는 걸
    점수에 넣지 않는다. 분모가 0이면 0.
    """
    denominator = total - unknown
    if denominator <= 0:
        return 0.0
    return round((met + 0.5 * partial) / denominator, 4)


def code_sort_key(code: str) -> tuple[int, ...]:
    """`2.10.1` 이 `2.2.1` 뒤에 오도록 항목 코드를 숫자 튜플로 만든다."""
    parts: list[int] = []
    for piece in code.split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    return tuple(parts)
