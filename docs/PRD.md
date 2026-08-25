# CertPilot 시제품 기획서 (v0.1)

> ISMS-P 준비·유지 코파일럿 — 모의심사 · 문서 초안 · 증적 커넥터 · 검수 워크플로
> 작성일 2026-08-25 · 대상 기간 2026.9 ~ 2026.12 · 빌드 도구 Claude Code

---

## 0. 이 문서 사용법

이 문서는 사람이 읽는 기획서이면서, Claude Code가 그대로 읽고 작업할 수 있게 쓴 스펙이다.

1. 새 리포지토리 루트에 `docs/PRD.md`로 저장한다.
2. 부록 A의 `CLAUDE.md`를 리포지토리 루트에 둔다. (Claude Code는 세션마다 루트의 `CLAUDE.md`를 먼저 읽는다. 참고: https://code.claude.com/docs/en/memory)
3. 부록 B의 작업 순서(Task 0 → 12)를 한 번에 하나씩 Claude Code에 붙여 넣는다. 태스크마다 "완료 조건"이 있으니 그걸 통과해야 다음으로 넘어간다.
4. 이 문서에서 `[결정 필요]`로 표시된 곳은 팀이 정해야 한다. Claude Code에게 맡기지 말 것.

문서 수정 이력은 맨 아래에 남긴다.

---

## 1. 시제품 목표

**한 문장:** 중소기업이 자기 문서와 AWS 계정을 연결하면, ISMS-P 인증기준 101개 항목 기준으로 무엇이 빠졌는지 찾아주고, 운영명세서 초안을 쓰고, 클라우드 증적을 자동으로 모아 심사원 검수를 거쳐 내보내는 것을 **끝까지 한 번 보여준다.**

### 데모 성공 기준 (이 5개가 되면 시제품 완성)

| # | 기준 | 확인 방법 |
|---|---|---|
| D1 | 가상 회사 문서 10여 개를 올리고 "모의심사 실행"을 누르면 10분 안에 101개 항목 판정이 나온다 | 데모 시드 데이터로 재현 |
| D2 | 판정 하나를 클릭하면 근거가 된 문서 문장(청크)이 하이라이트로 보이고, 근거 없는 항목은 "판단불가"로 표시된다 | 환각 방지의 핵심 |
| D3 | "운영명세서 초안 생성"을 누르면 101개 행이 채워진 DOCX가 나오고, 정보 없는 칸은 `[확인 필요]`로 남는다 | 파일 열어서 확인 |
| D4 | AWS 샌드박스 계정을 연결하면 10개 점검 결과가 증적으로 저장되고 항목에 매핑된다. 설정을 일부러 바꾸면 다음 수집에서 "변경 감지" 알림이 뜬다 | 라이브 데모 |
| D5 | 심사원 역할로 로그인해 초안을 "승인"하기 전까지 고객은 다운로드할 수 없다 | 권한 테스트 |

### 시제품이 증명해야 하는 주장 (발표 슬라이드와 1:1)

- "규칙으로 판정 가능한 건 규칙으로, 해석이 필요한 건 LLM + 심사원 검수로" → D2, D4, D5
- "인증을 받고 끝이 아니라 매년 돌아가는 구조" → D4의 변경 감지
- "기존 에이전트 인프라 재사용" → 비동기 큐, 규칙+LLM 하이브리드, AWS 배포

---

## 2. 범위

### In (시제품)

- 인증기준 지식베이스: ISMS-P 101개 항목 + 간편인증 플래그 + 항목별 결함 사례
- 문서 업로드·분석: PDF / DOCX / XLSX / MD → 텍스트 → 청크 → 벡터 검색
- 모의심사 에이전트: 항목별 판정(충족 / 부분충족 / 미충족 / 판단불가) + 근거 청크 + 예상 결함 + 개선 권고
- 문서 코파일럿: 운영명세서 초안(101행) + 정보보호 정책 초안 1종
- AWS 증적 커넥터(읽기 전용): 점검 10개, 스냅샷 저장, 변경 감지
- 검수 워크플로: 심사원 역할, 승인/반려, 감사 로그
- 갭 리포트(XLSX/PDF), 증적 패키지(ZIP) 내보내기
- 유지 대시보드(기본): 장별 준비도 점수, 변경 알림, 사후심사 D-day

### Out (시제품에서 하지 않음)

- 인증 신청·심사 대행, 심사기관 연동
- 취약점 스캐닝, 침입 탐지, 보안관제 (우리는 보안 솔루션이 아니다)
- 온프레미스·NCP·Azure 커넥터
- 결제, 요금제, 멀티테넌트 과금
- 모바일 앱

### Later (12월 이후 후보)

- GitHub 커넥터(브랜치 보호, 2FA, 시크릿 스캐닝) — 부록 B Task 7b로 준비만 해 둔다
- Google Workspace 커넥터(계정·2단계 인증)
- 개인정보 처리 화면 자동 점검(기존 브라우저 에이전트 재사용)
- ISMS 강화 기준·개정 고시 반영 파이프라인

---

## 3. 사용자와 역할

| 역할 | 누구 | 할 수 있는 것 |
|---|---|---|
| `org_admin` | 고객사 CTO/담당자 | 프로젝트 생성, 문서 업로드, 커넥터 연결, 모의심사 실행, 승인된 산출물 다운로드 |
| `org_member` | 고객사 팀원 | 문서 업로드, 결과 열람 |
| `reviewer` | 인증심사원 / 컨설턴트 (외부) | 검수 큐, 초안 편집·승인·반려, 코멘트 |
| `operator` | CertPilot 운영자(우리) | 모든 조직 열람, 지식베이스 관리, 골든셋 평가 실행 |

인증: 이메일 + 비밀번호(bcrypt) + 세션 쿠키. 소셜 로그인 없음. 조직 단위 데이터 격리는 모든 쿼리에 `org_id` 필터를 강제한다.

---

## 4. 핵심 시나리오 (데모 3분)

```
[0:00] org_admin 로그인 → 프로젝트 "데모핀테크 / ISMS-P / 간편인증" 생성
[0:20] 문서 12개 업로드(정보보호 정책, 조직도, 자산목록, 위험평가서, 개인정보처리방침 …) → 파싱 완료 표시
[0:50] AWS 커넥터 연결(샌드박스 계정 Role ARN) → 점검 10개 수집 완료, 항목 매핑 표시
[1:10] "모의심사 실행" → 진행률 바 → 완료. 장별 준비도: 1장 60% / 2장 45% / 3장 30%
[1:40] 미충족 항목 클릭(예: 2.5.3 사용자 인증) → 근거: "IAM 사용자 7명 중 MFA 미설정 4명" + 문서 청크 하이라이트 → 예상 결함 문장 → 개선 권고
[2:10] "운영명세서 초안 생성" → 검수 대기 상태. 다운로드 버튼 비활성
[2:30] reviewer 로그인 → 검수 큐에서 초안 열기 → 한 행 수정 → 승인 → org_admin 화면에서 다운로드 활성화
[2:50] 대시보드: 변경 감지 알림("S3 버킷 1개 퍼블릭 액세스 차단 해제됨"), 사후심사 D-312
```

이 시나리오가 `make demo` 한 번으로 재현돼야 한다(부록 B Task 11).

---

## 5. 아키텍처와 기술 스택

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

### 스택 결정

| 영역 | 선택 | 이유 |
|---|---|---|
| 백엔드 | Python 3.12 · FastAPI · SQLAlchemy 2 · Alembic | LLM·문서 처리 라이브러리가 전부 파이썬. 시제품은 단일 언어가 빠르다 |
| 비동기 | Celery + Redis | 팀 경험 있음. 모의심사·수집은 전부 백그라운드 잡 |
| DB | PostgreSQL 16 + pgvector | 벡터 검색을 별도 인프라 없이 처리 |
| 프런트 | Next.js 14 (App Router) · TypeScript · Tailwind · shadcn/ui | Claude Code가 가장 안정적으로 다루는 조합 |
| 문서 파싱 | pdfplumber, python-docx, openpyxl | 한글 PDF는 pdfplumber → 실패 시 pypdf 폴백 |
| 클라우드 | boto3 (AWS) | 읽기 전용. `SecurityAudit` 관리형 정책 + 외부 ID |
| LLM | Anthropic API (판정·생성: `claude-sonnet-5`, 분류·마스킹 보조: `claude-haiku-4-5-20251001`) | 모델명은 https://docs.claude.com 에서 최신 확인. 프로바이더 교체 가능하게 `llm/` 모듈로 감싼다 |
| 배포 | Docker Compose(개발) → AWS EC2 1대 + RDS(데모) | 시제품은 단일 인스턴스면 충분 |
| 테스트 | pytest · Playwright(e2e 3개) · ruff · mypy | Task마다 테스트 필수 |

`[결정 필요]` 기존 Spring Boot 코드를 API로 살릴지. 살리려면 Spring Boot가 게이트웨이, FastAPI가 AI 서비스로 분리되지만 시제품 속도가 느려진다. 기본안은 FastAPI 단일.

---

## 6. 데이터 모델

### 엔티티

| 테이블 | 핵심 컬럼 | 비고 |
|---|---|---|
| `organizations` | id, name, plan(simplified/standard) | 테넌트 |
| `users` | id, org_id(null 가능: reviewer/operator), email, role, password_hash | |
| `projects` | id, org_id, name, cert_type(ISMS / ISMS-P), is_simplified(bool), scope_text, audit_due_date | 인증범위 |
| `criteria` | code(PK, 예 `2.5.3`), chapter(1/2/3), section, title, requirement, checkpoints[] , defect_examples[], is_simplified, version(`2023`) | 지식베이스. 안내서에서 로드 |
| `documents` | id, project_id, filename, s3_key, mime, status(uploaded/parsed/failed), page_count, sha256 | |
| `chunks` | id, document_id, seq, text, page, embedding(vector 1536), token_count | pgvector 인덱스 |
| `connectors` | id, project_id, type(aws/github), config_json(암호화), status, last_collected_at | 자격증명은 KMS/환경변수 암호화 |
| `evidence` | id, project_id, connector_id(null=문서), source(aws.iam / aws.s3 / doc), check_id, criterion_codes[], status(pass/fail/warn/unknown), payload_json, collected_at, snapshot_id | 증적 원본 |
| `assessments` | id, project_id, status(queued/running/done/failed), started_at, finished_at, model, cost_usd, summary_json | 모의심사 1회 |
| `findings` | id, assessment_id, criterion_code, status(met/partial/unmet/unknown), confidence(0~1), rationale, evidence_chunk_ids[], evidence_ids[], predicted_defect, recommendation, decided_by(rule/llm/reviewer) | 항목별 판정 |
| `drafts` | id, project_id, kind(sow / policy), version, status(draft/in_review/approved/returned), content_json, docx_s3_key, created_by | 운영명세서(sow)·정책 |
| `review_tasks` | id, draft_id, reviewer_id, status, comment, decided_at | 검수 |
| `audit_logs` | id, org_id, user_id, action, target, meta_json, created_at | 모든 승인·다운로드·커넥터 변경 기록 |
| `alerts` | id, project_id, type(drift/due/defect), message, evidence_id, read_at | 대시보드 알림 |

### `data/criteria/criteria.json` 스키마

```json
{
  "version": "2023",
  "source": "KISA·개인정보위 ISMS-P 인증기준 안내서 (파일명, 발행일 기입)",
  "items": [
    {
      "code": "2.5.3",
      "chapter": 2,
      "section": "2.5 인증 및 권한관리",
      "title": "사용자 인증",
      "requirement": "…안내서 인증기준 본문…",
      "checkpoints": ["…주요 확인사항 1…", "…2…"],
      "defect_examples": ["…안내서의 결함사례…"],
      "is_simplified": true,
      "evidence_hints": ["MFA 설정 현황", "로그인 실패 정책"]
    }
  ]
}
```

규칙: `items` 길이는 정확히 101, 장별 16 / 64 / 21. 테스트로 고정한다. **항목 본문을 LLM이 지어내게 하지 않는다. 반드시 공식 안내서 PDF에서 추출한다.**

### 판정(Finding) 출력 스키마 — LLM이 반드시 이 JSON만 반환

```json
{
  "criterion_code": "2.5.3",
  "status": "unmet",
  "confidence": 0.72,
  "rationale": "정책 문서 3.2절은 관리자 MFA를 요구하나(chunk:c_118), AWS IAM 증적에서 사용자 7명 중 4명 MFA 미설정(evidence:e_42).",
  "evidence_chunk_ids": ["c_118"],
  "evidence_ids": ["e_42"],
  "predicted_defect": "정책에서 정한 관리자 다중인증이 실제 클라우드 계정에 적용되어 있지 않음",
  "recommendation": "IAM 사용자 전원 MFA 강제 정책 적용 후 증적 재수집",
  "missing_info": []
}
```

- `evidence_chunk_ids`와 `evidence_ids`가 모두 비어 있으면 서버가 `status`를 강제로 `unknown`으로 바꾼다. (환각 방지 1차 장치)
- `rationale` 안의 `chunk:` / `evidence:` 참조가 실제 존재하지 않으면 판정을 폐기하고 재시도한다. (2차 장치)

---

## 7. 기능 명세

각 기능은 **설명 → 규칙 → 완료 조건(AC)** 순서. AC는 그대로 테스트 케이스가 된다.

### F1. 인증기준 지식베이스

- `scripts/kb_build.py`: `data/raw/*.pdf`(공식 안내서)를 읽어 `criteria.json` 생성. 항목 코드 정규식(`^\d\.\d{1,2}\.\d{1,2}`)으로 분리, 인증기준·주요 확인사항·결함사례 블록을 추출.
- 간편인증 항목 목록은 `data/criteria/simplified_codes.txt`에서 로드해 플래그. `[결정 필요]` 고시 별표 기준 목록 확보(보안 교수님).
- AC: 101개 로드, 장별 개수 일치, 코드 중복 없음, 모든 항목 `requirement` 길이 > 50자. `pytest tests/test_kb.py` 통과.

### F2. 문서 업로드·분석

- 업로드 → S3 저장 → Celery `ingest` → 텍스트 추출 → 500토큰 청크(100 오버랩) → 임베딩 → `chunks` 저장.
- 개인정보 마스킹: 임베딩·LLM 전송 전 주민등록번호, 전화번호, 이메일, 카드번호 패턴을 `[MASKED:type]`으로 치환. 원문은 S3에만.
- 규칙: 20MB 초과 거부, 암호 걸린 PDF는 `failed` + 사유.
- AC: 12개 샘플 문서 파싱 성공률 100%, 마스킹 테스트 8개 통과, 항목 코드로 관련 청크 top-5 검색 API(`GET /projects/{id}/chunks/search?criterion=2.5.3`) 동작.

### F3. 모의심사 에이전트

- `POST /projects/{id}/assessments` → Celery `assess` 잡. 항목 101개를 병렬(동시 5)로 처리.
- 항목별 파이프라인:
  1. **규칙 판정**: 해당 항목에 매핑된 커넥터 증적이 있으면 규칙표(`data/rules/aws_rules.yaml`)로 pass/fail 산출.
  2. **검색**: 항목 requirement + checkpoints를 쿼리로 청크 top-8 검색.
  3. **LLM 판정**: 시스템 프롬프트(심사원 역할, 출력 스키마, "근거 없으면 unknown") + 항목 정의 + 결함 사례 + 청크 + 규칙 판정 결과 → JSON.
  4. **후처리**: 스키마 검증, 근거 참조 검증, 규칙 fail이면 LLM이 met이라 해도 `unmet`으로 덮어쓰기(`decided_by=rule`).
- 비용 로그: 항목별 토큰·비용 기록. 1회 실행 목표 비용 `[결정 필요]` (초기 상한 5달러).
- AC: 데모 시드로 10분 내 완료, 판정 분포에 `unknown` 존재(근거 없는 항목), 재실행 시 동일 입력이면 판정 일치율 90% 이상(temperature 0), 근거 참조 검증 실패 시 재시도 로그 확인.

### F4. 문서 코파일럿

- **운영명세서(sow)**: 항목별 행 = {항목코드, 운영 현황(초안), 관련 문서·증적 목록, 담당 부서 `[확인 필요]`, 비고}. 현황 초안은 F3 판정의 rationale과 청크를 재료로 생성. 정보 없으면 `[확인 필요]`.
- **정보보호 정책 초안**: `data/templates/policy_ko.md` 뼈대에 조직 정보(회사명, 서비스, CISO 지정 여부 등 프로젝트 설정)를 채움. 없는 값은 `[확인 필요]`.
- 생성 즉시 `status=in_review`. DOCX 변환(python-docx). 승인 전 다운로드 API는 403.
- AC: 101행 생성, `[확인 필요]` 개수 리포트, DOCX 열림, 승인 전 다운로드 403 테스트.

### F5. AWS 증적 커넥터 (읽기 전용)

- 연결 방식: 고객 계정에 CloudFormation 템플릿으로 `CertPilotReadOnly` 역할 생성(정책 `SecurityAudit` + 필요 최소 `Describe/List/Get`, 외부 ID 필수). 개발 환경은 액세스 키 허용.
- 점검 목록 10개와 항목 매핑은 §9 표.
- 수집 잡 `collect`: 하루 1회 스케줄 + 수동 실행. 결과는 `evidence` + `snapshot_id`. 직전 스냅샷과 diff → 변화가 있으면 `alerts(type=drift)`.
- AC: 샌드박스 계정에서 10개 점검 모두 결과 반환, 스냅샷 2회 후 설정 변경 시 drift 알림 1건 생성, 자격증명이 로그·DB 평문에 남지 않음(테스트).

### F6. 검수 워크플로

- reviewer는 자기에게 배정된 `review_tasks`만 본다. 초안 편집은 `content_json` 단위, 버전은 승인 때마다 +1.
- 승인 → `drafts.status=approved`, 다운로드 활성화, `audit_logs` 기록. 반려 → 코멘트 필수, org_admin에게 알림.
- AC: 권한 매트릭스 테스트(§3 표 그대로), 승인/반려 e2e 1개.

### F7. 갭 리포트·증적 패키지

- 갭 리포트 XLSX: 시트1 요약(장별 met/partial/unmet/unknown 수, 준비도 %), 시트2 항목별 판정 전체, 시트3 예상 결함 목록(우선순위 = unmet & confidence 높은 순).
- 증적 패키지 ZIP: `/{criterion_code}/` 폴더에 매핑된 증적 JSON + 문서 청크 출처 목록 + `README.md`(수집 시각, 계정 ID 마스킹).
- AC: 두 파일 생성, 리포트 수치와 DB 집계 일치 테스트.

### F8. 유지 대시보드

- 카드: 장별 준비도, 미충족 Top 5, 최근 drift 알림, 사후심사 D-day(`projects.audit_due_date`), 검수 대기 건수.
- 준비도 % = (met + 0.5·partial) / (101 − unknown). unknown은 분모에서 뺀다(모르는 걸 점수에 넣지 않는다).
- AC: 시드 데이터 기준 숫자 검증 테스트.

### F9. 운영자 도구

- 지식베이스 버전 교체(`criteria.json` 재로드), 골든셋 평가 실행(§8), 조직별 LLM 비용 조회.

---

## 8. AI 설계

### 원칙

1. **판정의 근거는 항상 데이터에 있어야 한다.** 근거 참조 없는 판정은 `unknown`.
2. **규칙이 LLM을 이긴다.** 커넥터가 fail이면 LLM 의견과 무관하게 unmet.
3. **LLM은 초안만 쓴다.** 최종본은 reviewer 승인으로만 만들어진다.
4. **개인정보는 LLM에 보내지 않는다.** 마스킹 후 전송, 프로바이더 학습 미사용 설정.
5. **temperature 0, JSON 스키마 강제, 실패 시 최대 2회 재시도.**

### 프롬프트 구조 (`apps/api/app/llm/prompts/assess.md`)

```
[시스템] 당신은 ISMS-P 인증심사원이다. 아래 항목의 인증기준·주요 확인사항·결함사례와 제공된 근거(문서 청크, 클라우드 증적)만으로 판정한다.
근거에 없는 사실을 가정하지 않는다. 근거가 부족하면 status=unknown, missing_info에 무엇이 필요한지 적는다.
출력은 지정된 JSON 스키마만.
[사용자]
## 항목 {code} {title}
인증기준: …
주요 확인사항: …
결함사례: …
## 규칙 판정 결과
{rule_results or "없음"}
## 근거
[chunk:c_118 | 정보보호정책 v2.1 p.7] "…"
[evidence:e_42 | aws.iam.mfa | 2026-09-30] {"users":7,"mfa_enabled":3}
```

### 평가 (골든셋)

- `data/eval/golden.yaml`: {criterion_code, project_fixture, expected_status, expert_note}. 초기 20개 → 보안 교수님·심사원 자문으로 채움.
- 지표: unmet 판정의 정밀도/재현율, unknown 비율, 근거 참조 유효율(목표 100%), 항목당 평균 비용.
- `make eval` 로 리포트 생성. 결과는 `docs/eval/YYYY-MM-DD.md`에 누적(발표용 숫자).

---

## 9. AWS 점검 10개와 항목 매핑

| check_id | 점검 내용 | boto3 | pass 조건 | 매핑 항목 |
|---|---|---|---|---|
| aws.iam.root_mfa | 루트 계정 MFA | iam.get_account_summary | AccountMFAEnabled=1 | 2.5.3 사용자 인증 |
| aws.iam.user_mfa | IAM 사용자 MFA 비율 | iam.list_users + list_mfa_devices | 콘솔 사용자 100% | 2.5.3 |
| aws.iam.password_policy | 비밀번호 정책 | iam.get_account_password_policy | 길이≥8, 복잡도, 만료 설정 | 2.5.4 비밀번호 관리 |
| aws.iam.key_age | 액세스 키 90일 초과 | iam.list_access_keys | 초과 키 0개 | 2.5.1 사용자 계정 관리 / 2.5.6 접근권한 검토 |
| aws.iam.admin_users | AdministratorAccess 부여 사용자 | iam.list_attached_user_policies | 목록 제공(경고), 3명 초과 시 fail | 2.5.5 특수 계정 및 권한 관리 |
| aws.cloudtrail.enabled | 전 리전 트레일 + 로그 무결성 검증 | cloudtrail.describe_trails | IsMultiRegionTrail & LogFileValidationEnabled | 2.9.4 로그 및 접속기록 관리 |
| aws.s3.public_block | 버킷 퍼블릭 액세스 차단 | s3.get_public_access_block | 모든 버킷 4개 옵션 true | 2.10.2 클라우드 보안 / 2.6.2 정보시스템 접근 |
| aws.s3.encryption | 버킷 기본 암호화 | s3.get_bucket_encryption | 전 버킷 설정 | 2.7.1 암호정책 적용 |
| aws.rds.encryption | RDS 저장 암호화·자동 백업 | rds.describe_db_instances | StorageEncrypted & BackupRetention≥7 | 2.7.1 / 2.9.3 백업 및 복구관리 |
| aws.ec2.open_sg | 0.0.0.0/0 으로 열린 22/3389/3306/5432 | ec2.describe_security_groups | 해당 규칙 0개 | 2.6.1 네트워크 접근 |

- 항목 코드는 2023년 개정 인증기준 기준. **`criteria.json` 로드 후 코드·명칭이 안내서와 일치하는지 검증하는 테스트를 둔다.** 불일치 시 매핑 파일(`data/rules/aws_rules.yaml`)만 고치면 되게 코드에 하드코딩하지 않는다.
- 후보(Later): GuardDuty(2.11.3), VPC Flow Logs(2.9.4), EBS 기본 암호화(2.7.1), SSM 패치 준수(2.10.8).

---

## 10. 우리 제품 자체의 보안 요구사항

고객의 보안 문서와 클라우드 권한을 다루는 제품이다. 시제품이어도 아래는 지킨다. (심사위원이 반드시 묻는다.)

- 클라우드 접근은 읽기 전용 역할 + 외부 ID. 쓰기 권한 요청 금지.
- 커넥터 자격증명·토큰은 KMS(또는 개발 환경에서는 Fernet 키) 암호화 저장. 로그·에러 메시지에 절대 출력 금지(테스트로 강제).
- 저장 암호화: RDS·S3 SSE 기본, 전송 HTTPS만.
- 테넌트 격리: 모든 데이터 접근은 `org_id` 스코프. 크로스 테넌트 접근 테스트 3개.
- LLM 전송 전 마스킹, 프로바이더 학습 미사용, 프롬프트·응답은 30일 후 삭제.
- 감사 로그: 로그인, 다운로드, 승인, 커넥터 변경, 역할 변경.
- 비밀은 `.env`/시크릿 매니저. 리포지토리에 키가 들어가면 CI 실패(gitleaks).
- 데이터 보존: 프로젝트 삭제 시 S3·DB 30일 내 완전 삭제.

---

## 11. 비기능 요구사항

| 항목 | 목표 |
|---|---|
| 모의심사 1회 | 101항목 10분 이내(문서 12개, 청크 1,000개 기준) |
| 문서 파싱 | 20MB PDF 2분 이내 |
| 커넥터 수집 | 계정당 3분 이내 |
| 가용성 | 데모 기간 단일 인스턴스, 일일 백업 |
| 관측 | 구조화 로그(JSON), 잡 상태 대시보드(Flower), LLM 비용 일 집계 |
| 테스트 | Task마다 단위 테스트, e2e 3개(업로드→모의심사, 승인 플로, drift 알림) |
| 코드 품질 | ruff, mypy(strict 아님), 타입 힌트 필수 |

---

## 12. 로드맵 (12주)

| 주차 | 산출물 | 데모 포인트 |
|---|---|---|
| W1~2 (9월 초) | 리포 스캐폴드, Docker Compose, 인증/RBAC, 지식베이스 로드(F1) | 101개 항목 화면 |
| W3~4 | 문서 업로드·검색(F2), 모의심사 v1(F3) | 판정 + 근거 하이라이트 |
| W5~6 (10월) | 갭 리포트(F7), 대시보드 기본(F8) | 준비도 점수 |
| W7~8 | AWS 커넥터(F5), 규칙 판정 병합, drift 알림 | 라이브 계정 연결 |
| W9~10 (11월) | 문서 코파일럿(F4), 검수 워크플로(F6), 증적 패키지 | 승인 플로 |
| W11~12 | 데모 시드·`make demo`, 골든셋 평가, 배포, 컨설팅사 파일럿 피드백 반영 | 발표용 3분 데모 |

마일스톤: **M1(W4) 모의심사 돌아감 · M2(W8) 커넥터 돌아감 · M3(W12) 데모 완성.** 한성대 프로그램 중간 점검이 있으면 M1·M2 시점에 맞춘다.

---

## 13. 리스크와 대응

| 리스크 | 영향 | 대응 |
|---|---|---|
| 안내서 PDF 파싱 품질(표·병합 셀) | 지식베이스 오류 | 파싱 결과를 사람이 1회 전수 검토, `criteria.json`을 소스 오브 트루스로 커밋 |
| 인증기준 개정(ISMS 강화 기준 등) | 항목 코드·내용 변경 | `version` 필드, 매핑 파일 분리, 재로드 도구(F9) |
| LLM 판정 신뢰도 | 잘못된 unmet/met | 규칙 우선, 근거 강제, 골든셋 평가 수치 공개, 검수 필수 |
| 한글 PDF·HWP | HWP 미지원 | 시제품은 HWP → PDF 변환 안내. Later에 hwp 파서 검토 |
| AWS 샌드박스 비용·권한 | 데모 실패 | 프리티어 계정 1개, CloudFormation 역할 템플릿을 리포에 포함 |
| 자격증명 유출 | 치명적 | §10 강제 테스트, gitleaks, 읽기 전용 |
| 시간 부족 | M3 미달 | F4·F6은 운영명세서만(정책 초안 후순위), GitHub 커넥터 제외 |

---

## 14. 오픈 이슈 (보안 교수님·심사원 자문에서 확인)

1. 간편인증 대상 항목 정확한 목록(고시 별표)과 2026년 현재 유효 버전
2. 운영명세서의 실제 심사 제출 양식(엑셀 서식) — 우리 DOCX/XLSX가 그 서식을 따르게
3. 심사원이 클라우드 증적을 어떤 형태로 받는지(캡처 vs JSON vs 콘솔 열람)
4. 골든셋 20개 항목 판정에 참여 가능한지, 어떤 조건으로
5. 고객 문서를 외부 LLM에 보내는 것에 대한 심사·계약상 이슈(국내 리전 필요 여부)
6. ISMS 강화 기준·2026~27 개정 일정

---

## 부록 A. `CLAUDE.md` 초안 (리포지토리 루트에 그대로 저장)

```markdown
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
- 부록 C 참조 (@docs/PRD.md)
```

## 부록 B. Claude Code 작업 순서

각 태스크를 하나씩 붙여 넣는다. 태스크 첫 줄에 "docs/PRD.md의 §N과 부록 B Task N을 읽고 계획을 먼저 보여줘"를 넣으면 Claude Code가 계획 → 확인 → 구현 순서로 간다.

| Task | 지시(요약) | 완료 조건 |
|---|---|---|
| 0 | PRD 전체와 CLAUDE.md를 읽고, 이해한 범위·질문·리스크를 정리해 `docs/plan.md`로 써라. 코드는 쓰지 마라. | plan.md에 질문 목록 존재, 팀이 답변 |
| 1 | 모노레포 스캐폴드: 부록 C 구조, docker-compose(postgres+pgvector, redis, minio), FastAPI 헬스 엔드포인트, Next.js 빈 화면, Makefile(dev/check), GitHub Actions(lint+test). | `make dev` 후 `/health` 200, CI 녹색 |
| 2 | F1 지식베이스: `scripts/kb_build.py` + `criteria.json` + `tests/test_kb.py`. 안내서 PDF는 `data/raw/`에 사람이 넣는다. 표 파싱 실패 항목은 리포트로 남겨라. | 101개, 16/64/21, 코드 중복 0 |
| 3 | 데이터 모델·마이그레이션(§6 전부), 인증(이메일/비밀번호/세션), RBAC 미들웨어, 조직·프로젝트 CRUD, 크로스 테넌트 테스트 3개. | 권한 매트릭스 테스트 통과 |
| 4 | F2 문서 인제스트: 업로드 API, Celery ingest, 파서(pdf/docx/xlsx/md), 마스킹, 청킹, 임베딩, 청크 검색 API. 샘플 문서 12개는 `data/samples/`에 가상 회사 기준으로 직접 생성하라(실제 기업 문서 금지). | 파싱 100%, 마스킹 테스트 8개 |
| 5 | F3 모의심사: 규칙 판정 인터페이스(빈 규칙표 허용) → 검색 → LLM 판정(스키마 강제) → 후처리(근거 검증, unknown 강제) → findings 저장. 비용 로그. `llm/` 모듈로 프로바이더 추상화. | 시드로 10분 내 완료, 근거 검증 테스트 |
| 6 | 갭 리포트 화면: 프로젝트 페이지, 모의심사 실행·진행률, 판정 테이블(필터·정렬), 상세 드로어(근거 하이라이트), XLSX 내보내기(F7 일부). | e2e "업로드→모의심사→리포트" 통과 |
| 7 | F5 AWS 커넥터: CloudFormation 역할 템플릿, 점검 10개(§9), 스냅샷·diff·drift 알림, 규칙 판정 병합, 자격증명 암호화·로그 마스킹 테스트. moto로 단위 테스트. | 10개 점검, drift 알림 테스트 |
| 7b | (선택) GitHub 커넥터 인터페이스만: 브랜치 보호·2FA·시크릿 스캐닝 점검 3개, 매핑 2.8.5/2.5.3/2.11.2. | 인터페이스+테스트 |
| 8 | F4 문서 코파일럿: 운영명세서 101행 생성, 정책 초안 템플릿 채움, `[확인 필요]` 카운트, DOCX 변환, in_review 상태, 승인 전 403. | DOCX 열림, 403 테스트 |
| 9 | F6 검수 워크플로: reviewer 화면, 큐, 편집, 승인/반려, 버전, 감사 로그, 알림. | e2e "승인 플로" 통과 |
| 10 | F8 대시보드 + F7 증적 패키지 ZIP + 알림 목록 + 사후심사 D-day. | 준비도 수치 테스트, e2e "drift 알림" |
| 11 | `make demo`: 데모핀테크 시드(조직·사용자 3역할·문서 12개·모의심사 결과·초안·알림)를 한 번에 생성. 시나리오 §4 재현 스크립트. | 클린 DB에서 3분 시나리오 재현 |
| 12 | `make eval`: 골든셋 포맷, 평가 러너, 리포트 `docs/eval/`. 배포 스크립트(EC2+RDS), 백업 크론, README. | 리포트 생성, 스테이징 URL 접속 |

Claude Code에 붙여 넣는 형식 예:

```
docs/PRD.md §7 F1과 부록 B Task 2를 읽고, 먼저 구현 계획(파일 목록, 파싱 전략, 실패 처리)을 보여줘.
내가 승인하면 구현하고, tests/test_kb.py가 통과하고 make check가 녹색인 상태로 보고해.
data/raw/에 안내서 PDF는 내가 넣어뒀어. 항목 본문을 절대 지어내지 마.
```

## 부록 C. 리포지토리 구조

```
certpilot/
├─ CLAUDE.md
├─ Makefile
├─ docker-compose.yml
├─ .github/workflows/ci.yml
├─ docs/
│  ├─ PRD.md                 ← 이 문서
│  ├─ plan.md                ← Task 0 산출물
│  ├─ adr/                   ← 결정 기록 (스택, 매핑 변경 등)
│  └─ eval/                  ← 골든셋 평가 리포트
├─ data/
│  ├─ raw/                   ← 공식 안내서 PDF (gitignore)
│  ├─ criteria/criteria.json ← 소스 오브 트루스
│  ├─ criteria/simplified_codes.txt
│  ├─ rules/aws_rules.yaml   ← 점검 → 항목 매핑 + pass 조건
│  ├─ templates/policy_ko.md
│  ├─ samples/               ← 데모핀테크 가상 문서 12개
│  └─ eval/golden.yaml
├─ apps/
│  ├─ api/
│  │  ├─ app/{main.py, core/, models/, schemas/, api/, services/, llm/, connectors/, workers/}
│  │  ├─ alembic/
│  │  └─ tests/
│  └─ web/
│     ├─ app/(routes)  components/  lib/
│     └─ e2e/
├─ infra/{cloudformation/certpilot-readonly-role.yaml, deploy/}
└─ scripts/{kb_build.py, seed_demo.py, eval_run.py}
```

## 부록 D. 데모 시드 데이터

가상 회사 **데모핀테크**(직원 25명, AWS 단일 계정, 간편인증 대상). 아래 문서를 `data/samples/`에 가상으로 작성한다. 실제 기업 문서·개인정보를 넣지 않는다.

1. 정보보호 정책 v2.1 (일부 조항 누락 — 판정 다양성용)
2. 정보보호 조직도·CISO 지정 공문
3. 정보자산 목록(엑셀)
4. 위험평가 보고서(작년 것 — 갱신 필요 판정 유도)
5. 개인정보 처리방침
6. 개인정보 흐름도(오래된 버전)
7. 접근권한 검토 이력(분기 1회, 최근 누락)
8. 외부 위탁 계약서 요약
9. 보안 교육 이수 기록
10. 백업 정책·복구 테스트 결과(없음 → unmet 유도)
11. 변경관리 절차서
12. 침해사고 대응 절차서

AWS 샌드박스: 프리티어 계정 1개. 의도적으로 MFA 미설정 사용자 4명, 퍼블릭 액세스 차단 해제 버킷 1개, 90일 초과 키 2개를 만들어 둔다(데모 후 정리).

## 부록 E. 용어

| 용어 | 뜻 |
|---|---|
| 항목(criterion) | ISMS-P 인증기준의 한 조항. 코드 예 `2.5.3` |
| 청크(chunk) | 문서를 잘라 임베딩한 조각. 판정 근거 단위 |
| 증적(evidence) | 커넥터가 수집한 사실. 규칙 판정의 입력 |
| 판정(finding) | 항목별 met/partial/unmet/unknown |
| 운영명세서(sow) | 항목별 운영 현황을 적은 심사 핵심 문서 |
| drift | 직전 스냅샷 대비 클라우드 설정 변화 |

---

## 수정 이력

- v0.1 (2026-08-25) 초안. 시제품 범위·스택·데이터 모델·기능·AI 설계·로드맵·Claude Code 작업 순서.
