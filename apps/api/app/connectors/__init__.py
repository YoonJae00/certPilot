"""증적 커넥터 패키지 (PRD §7 F5).

읽기 전용이다. 이 패키지 안에서 클라우드 **쓰기 API 를 호출하지 않는다**
(CLAUDE.md 절대 규칙 4). 허용되는 호출은 Describe / List / Get 과
`sts:AssumeRole` · `sts:GetCallerIdentity` 뿐이다.
"""

from app.connectors.mapping import CheckMapping, MappingError, load_check_mappings

__all__ = ["CheckMapping", "MappingError", "load_check_mappings"]
