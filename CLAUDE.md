# CertPilot

ISMS-P 준비·유지 코파일럿 시제품. 스펙은 @docs/PRD.md — 작업 전에 반드시 읽는다.

## 스택
- apps/api: Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Celery, pgvector, boto3
- apps/web: Next.js 14 (App Router), TypeScript, Tailwind, shadcn/ui
- infra: docker-compose (postgres16+pgvector, redis, minio(S3 대용))
- 명령: `make dev` (전체 기동) · `make check` (ruff+mypy+pytest+eslint) · `make demo` (데모 시드) · `make eval` (골든셋)

## 절대 규칙
1. ISMS-P 인증기준 항목(코드·명칭·본문)을 지어내지 않는다. 항상 data/criteria/criteria.json에서 읽는다. 없는 항목은 만들지 말고 물어본다.
2. LLM 판정은 근거 참조(chunk_id/evidence_id)가 없으면 status=unknown 으로 서버에서 강제한다. 이 로직을 우회하는 코드를 쓰지 않는다.
3. 클라우드 자격증명·토큰·개인정보는 로그, 테스트 픽스처, 커밋에 절대 넣지 않는다. .env.example만 커밋한다.
4. AWS 호출은 읽기 전용(Describe/List/Get)만. 쓰기 API를 호출하는 코드를 만들지 않는다.
5. 모든 DB 접근은 org_id 스코프를 거친다. 새 쿼리를 쓰면 크로스 테넌트 테스트를 같이 쓴다.
6. 스키마(모델·마이그레이션) 변경, 새 외부 라이브러리 추가, 요금이 드는 API 호출 추가는 먼저 계획을 보여주고 확인받는다.
7. 각 Task는 테스트가 통과하고 `make check`가 녹색일 때만 완료로 보고한다. 못 한 것은 "미완"으로 명시한다.

## 코드 규칙
- 코드·식별자는 영어, 주석·커밋 메시지·사용자 문구는 한국어.
- 타입 힌트 필수. 예외는 삼키지 않는다.
- API 응답은 Pydantic 모델. 프런트는 openapi로 타입 생성.
- 커밋은 작게, 메시지 형식 `feat(api): …` / `fix(web): …` / `test: …`.

## 디렉터리
- @docs/PRD.md 부록 C 참조

## 모델 역할 분담: Advisor / Worker

너는 Advisor다. 판단에 집중하고, 구현 노동은 Worker에게 위임하라.

Advisor(너, 메인 세션)가 직접 하는 일:
- 요구사항 분석, 작업 분해, 설계 결정
- Worker에게 줄 작업 브리프 작성
- 결과 검증: diff 직접 확인, 테스트 직접 실행
- 최종 커밋 승인, 사용자 보고

Worker(opus 서브에이전트)에게 위임하는 일:
- 코드 작성과 수정, 테스트 작성 등 구현 작업 전부
- Agent 도구로 위임하고 model은 "opus"를 지정한다
- 서로 독립적인 작업은 병렬로 위임한다

브리프 기준:
- 네가 이미 파악한 컨텍스트를 담아 Worker가 재탐색하지 않게 하라
- 파일 경로, 프로젝트 컨벤션, 알려진 함정, 완료 기준(통과해야 할 테스트)을 포함하라

경계:
- Worker의 완료 보고를 그대로 믿지 마라. diff와 테스트로 직접 확인한 뒤 승인하라
- 검증 실패는 수정 브리프로 재위임하라. 직접 수정은 사소한 마무리에만 허용된다
- 한두 줄 수정처럼 위임 오버헤드가 더 큰 작업은 직접 처리해도 된다
