# CertPilot

중소기업이 자기 문서와 AWS 계정을 연결하면, ISMS-P 인증기준 101개 항목 기준으로 무엇이
빠졌는지 찾아주고, 운영명세서 초안을 쓰고, 클라우드 증적을 자동으로 모아 심사원 검수를
거쳐 내보내는 **ISMS-P 준비·유지 코파일럿 시제품**이다.

기획서는 [`docs/PRD.md`](docs/PRD.md), 구현 계획은 [`docs/plan.md`](docs/plan.md) 에 있다.

## 데모 성공 기준 (PRD §1)

| # | 기준 |
| --- | --- |
| **D1** | 가상 회사 문서 12개를 올리고 모의심사를 실행하면 10분 안에 101개 항목 판정이 나온다 |
| **D2** | 판정을 클릭하면 근거가 된 문서 청크가 보이고, 근거 없는 항목은 **판단불가**로 표시된다 |
| **D3** | 운영명세서 초안 생성 시 101개 행이 채워진 DOCX 가 나오고, 정보 없는 칸은 `[확인 필요]` 로 남는다 |
| **D4** | AWS 계정을 연결하면 점검 10개가 증적으로 저장·매핑되고, 설정이 바뀌면 **변경 감지** 알림이 뜬다 |
| **D5** | 심사원이 초안을 **승인**하기 전까지 고객은 다운로드할 수 없다 |

---

## 아키텍처 (PRD §5)

```
 [Next.js web] ──HTTPS──▶ [FastAPI api] ──▶ [PostgreSQL 16 + pgvector]
                                │                 ▲
                                ├──▶ [Redis] ──▶ [Celery worker]
                                │                 │  ├─ ingest (파싱·청킹·임베딩)
                                │                 │  ├─ assess (모의심사)
                                │                 │  ├─ draft  (문서 생성)
                                │                 │  └─ collect(AWS 증적 수집, 스케줄)
                                ├──▶ [S3] (원본 문서, 증적 스냅샷, 산출물)
                                └──▶ [LLM API] (Anthropic Claude, 마스킹 후 전송)
```

| 영역 | 스택 |
| --- | --- |
| 백엔드 | Python 3.12 · FastAPI · SQLAlchemy 2 · Alembic · Celery |
| 프런트 | Next.js 14 (App Router) · TypeScript · Tailwind · shadcn/ui |
| 저장소 | PostgreSQL 16 + pgvector · Redis · S3(로컬은 MinIO) |
| LLM | Anthropic Claude. 키가 없으면 **결정적 Fake 프로바이더**로 동작한다 |
| 클라우드 | boto3, **읽기 전용**(Describe/List/Get)만 호출한다 |

판정 파이프라인은 세 겹이다. **규칙 판정**(클라우드 증적 pass/fail)이 LLM 을 이기고,
LLM 판정은 프롬프트에 제시된 근거 id 만 인용할 수 있으며, 근거가 하나도 없으면 서버가
`unknown` 으로 강제한다. 이 세 장치를 우회하는 코드는 쓰지 않는다
([`CLAUDE.md`](CLAUDE.md) 절대 규칙 2).

---

## 빠른 시작

### 사전 요구사항

| 도구 | 버전 | 확인 |
| --- | --- | --- |
| Docker Desktop | compose v2 포함 | `docker compose version` |
| [uv](https://docs.astral.sh/uv/) | 최신 | `uv --version` |
| Node.js | 20 이상 | `node --version` |
| Python | 3.12 (uv 가 알아서 맞춘다) | `uv python list` |

### 1) 환경 변수

```bash
cp .env.example .env
```

5432·6379·9000 포트를 다른 프로젝트가 쓰고 있으면 `.env` 의 `POSTGRES_PORT` 등을 바꾸고
`DATABASE_URL`·`REDIS_URL`·`S3_ENDPOINT` 의 포트도 같이 맞춘다.

`ANTHROPIC_API_KEY` 는 비워 둬도 된다. 비어 있으면 결정적 Fake 프로바이더가 뜨고,
데모·테스트는 그대로 재현된다.

### 2) 인프라 기동

```bash
make dev        # postgres(pgvector) · redis · minio
```

### 3) 스키마 + 데모 시드

```bash
make demo       # alembic upgrade head → 데모핀테크 시드 적재
```

`make demo` 한 번이면 PRD §4 의 3분 시나리오를 그대로 따라갈 수 있는 상태가 된다:
조직 1개, 계정 4개, 프로젝트 1개, 인증기준 101행, 문서 12개(파싱 완료), AWS 증적
스냅샷 2회(변경 감지 알림 1건), 완료된 모의심사 1회(판정 101개), 검수 대기 초안 1개.
여러 번 실행해도 결과가 같다(기존 데모 데이터를 지우고 다시 만든다).

### 4) 서버 실행

터미널 두 개에서:

```bash
make api        # http://localhost:8000  (API 문서 /docs)
make web        # http://localhost:3000
```

### 5) 데모 계정

비밀번호는 전부 `demo1234!` 다. 전부 가상 계정이며 운영에 쓰지 않는다.

| 이메일 | 역할 | 소속 | 볼 수 있는 것 |
| --- | --- | --- | --- |
| `admin@demofintech.kr` | 조직 관리자 | 데모핀테크 | 프로젝트 전체, 커넥터 등록, 초안 생성 |
| `member@demofintech.kr` | 조직 담당자 | 데모핀테크 | 프로젝트 조회, 문서 업로드 |
| `reviewer@certpilot.kr` | 심사원 | 무소속(플랫폼) | 검수 큐, 초안 승인·반려 |
| `operator@certpilot.kr` | 운영자 | 무소속(플랫폼) | 지식베이스 재적재 등 운영 도구 |

---

## make 타깃

| 타깃 | 하는 일 |
| --- | --- |
| `make help` | 타깃 목록 |
| `make dev` | 로컬 인프라(postgres·redis·minio) 기동 |
| `make down` | 로컬 인프라 정지 |
| `make api` | FastAPI 개발 서버 (`:8000`) |
| `make web` | Next.js 개발 서버 (`:3000`) |
| `make demo` | 데모핀테크 시드 적재 (PRD §4 재현) |
| `make eval` | 골든셋 평가 → `docs/eval/YYYY-MM-DD.md` |
| `make kb` | 안내서 PDF 에서 `data/criteria/criteria.json` 재생성 |
| `make check` | 린트·타입체크·테스트 전체 (`check-api` + `check-web`) |
| `make check-api` | ruff + mypy + pytest |
| `make check-web` | eslint + tsc |

---

## 테스트와 평가

### 테스트

```bash
make check
```

`check-api` 는 `certpilot_test` DB 를 자동으로 만들고 실제 Alembic 마이그레이션을
적용한 뒤 돌린다. 개발용 `certpilot` DB 는 건드리지 않는다. 다른 DB 로 돌리려면:

```bash
cd apps/api && DATABASE_URL='postgresql+psycopg://certpilot:certpilot@localhost:5432/certpilot_x' uv run pytest -q
```

e2e(Playwright)는 `cd apps/web && npm run test:e2e` 로 따로 돌린다.

### 골든셋 평가

```bash
make demo       # 대조할 모의심사 결과가 필요하다
make eval       # 시드가 이미 있으면 이것만
make eval SEED=1  # 시드가 없으면 자동으로 적재한 뒤 평가
```

[`data/eval/golden.yaml`](data/eval/golden.yaml) 의 기대 판정 20개와 데모 프로젝트의
최신 완료 모의심사를 대조해 PRD §8 지표를 계산하고 `docs/eval/YYYY-MM-DD.md` 에
리포트를 남긴다.

| 지표 | 뜻 |
| --- | --- |
| 전체 일치율 | 골든셋 케이스 중 기대 판정과 실제 판정이 같은 비율 |
| 미충족 정밀도·재현율·F1 | `unmet` 판정의 정확도 (골든셋 안에서) |
| 판단불가 비율 | 근거가 없어 `unknown` 이 된 항목 비율 |
| **근거 참조 유효율** | 판정이 인용한 chunk/evidence id 가 실제로 존재하는 비율. **목표 100%** |
| 항목당 평균 비용 | 실행 비용 ÷ 판정 수 |

> 초기 골든셋은 데모 시드의 사실관계에서 유도한 값이며 **심사원·보안 전문가 검증 전**이다
> (PRD §14 오픈 이슈 4). 일치율을 "전문가 정답과의 일치"로 읽으면 안 된다.

---

## 리포지토리 구조

```
certpilot/
├─ CLAUDE.md                  ← 코딩 규칙·절대 규칙 (작업 전 필독)
├─ Makefile                   ← 개발 명령 모음
├─ docker-compose.yml         ← 로컬 인프라 (postgres·redis·minio)
├─ docs/
│  ├─ PRD.md                  ← 기획서 (소스 오브 트루스)
│  ├─ plan.md                 ← 구현 계획·질문·리스크
│  └─ eval/                   ← 골든셋 평가 리포트 (날짜별)
├─ data/
│  ├─ raw/                    ← 안내서 원본 PDF (gitignore)
│  ├─ criteria/criteria.json  ← 인증기준 101개 — **본문의 유일한 출처**
│  ├─ rules/aws_rules.yaml    ← AWS 점검 10개 ↔ 항목 매핑 + pass 조건
│  ├─ samples/                ← 데모핀테크 가상 문서 12개
│  ├─ templates/              ← 문서 초안 템플릿
│  └─ eval/golden.yaml        ← 골든셋 20개
├─ apps/
│  ├─ api/                    ← FastAPI (app/{api,services,llm,connectors,workers,models})
│  │  ├─ alembic/             ← 마이그레이션
│  │  ├─ tests/               ← pytest
│  │  └─ Dockerfile
│  └─ web/                    ← Next.js 14 (app/, components/, lib/, e2e/)
│     └─ Dockerfile
├─ infra/
│  ├─ cloudformation/         ← 고객 계정용 읽기 전용 IAM 역할 템플릿
│  └─ deploy/                 ← EC2+RDS 데모 배포 (compose·deploy.sh·backup.sh)
└─ scripts/                   ← kb_build.py · gen_samples.py · seed_demo.py · eval_run.py
```

인증기준 항목의 코드·명칭·본문은 **`data/criteria/criteria.json` 에서만** 읽는다.
코드나 문서에 항목 본문을 지어내 넣지 않는다(CLAUDE.md 절대 규칙 1).

---

## 배포

EC2 1대 + RDS 데모 배포 절차는 [`infra/deploy/README-deploy.md`](infra/deploy/README-deploy.md)
에 있다. 보안그룹 설정, `.env.prod` 구성, `deploy.sh`(빌드→마이그레이션→재기동),
`backup.sh`(pg_dump → S3, 30일 보존), 고객 계정용 CloudFormation 역할 템플릿을 다룬다.

> 스크립트와 문서만 준비돼 있고 **실제 AWS 배포는 아직 하지 않았다.**

---

## 문서

- [`docs/PRD.md`](docs/PRD.md) — 시제품 기획서 (범위·기능 명세·AI 설계·로드맵)
- [`docs/plan.md`](docs/plan.md) — 구현 계획, 열린 질문과 채택한 기본값
- [`docs/eval/`](docs/eval/) — 골든셋 평가 리포트
- [`CLAUDE.md`](CLAUDE.md) — 코딩 규칙과 절대 규칙
- [`infra/deploy/README-deploy.md`](infra/deploy/README-deploy.md) — 배포 절차

---

## 라이선스

**미정.** 한성대학교 프로그램 산출물로 개발 중이며, 공개 라이선스를 아직 정하지 않았다.
외부 배포·재사용 전에 팀에 문의한다.

`data/raw/` 의 ISMS-P 인증기준 안내서(개인정보보호위원회·과학기술정보통신부, 2023.11)는
저작권이 발행 기관에 있으며 리포지토리에 커밋하지 않는다.
