# CertPilot 개발 명령 모음.
# api/web 은 컨테이너가 아니라 호스트에서 실행한다.

API_DIR := apps/api
WEB_DIR := apps/web

.PHONY: help dev down api web check check-api check-web kb demo eval

help: ## 사용 가능한 타깃을 보여준다
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

dev: ## 로컬 인프라(postgres·redis·minio) 기동
	docker compose up -d
	@echo ""
	@echo "인프라가 떴습니다."
	@echo "  postgres : localhost:5432 (certpilot/certpilot, db=certpilot)"
	@echo "  redis    : localhost:6379"
	@echo "  minio    : localhost:9000 (콘솔 http://localhost:9001)"
	@echo ""
	@echo "다음 단계: 터미널 두 개에서 'make api' 와 'make web' 을 실행하세요."

down: ## 로컬 인프라 정지
	docker compose down

api: ## FastAPI 개발 서버 실행 (http://localhost:8000)
	cd $(API_DIR) && uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

web: ## Next.js 개발 서버 실행 (http://localhost:3000)
	cd $(WEB_DIR) && npm run dev

check: check-api check-web ## 린트·타입체크·테스트 전체 실행
	@echo ""
	@echo "make check 통과."

check-api: ## api 검사 (ruff + mypy + pytest)
	@echo "== api: ruff =="
	cd $(API_DIR) && uv run ruff check .
	@echo "== api: mypy =="
	cd $(API_DIR) && uv run mypy app
	@echo "== api: pytest =="
	cd $(API_DIR) && uv run pytest -q

check-web: ## web 검사 (eslint + tsc)
	@echo "== web: eslint =="
	cd $(WEB_DIR) && npm run lint
	@echo "== web: tsc =="
	cd $(WEB_DIR) && npm run typecheck

kb: ## 안내서 PDF에서 data/criteria/criteria.json 재생성
	cd $(API_DIR) && uv run python ../../scripts/kb_build.py

demo: ## 데모 시드 적재 (데모핀테크 — PRD §4 3분 시나리오 재현)
	@echo "데모 시드를 적재합니다. 인프라(postgres·minio)가 떠 있어야 합니다 — 'make dev'."
	@echo "기존 '데모핀테크' 데이터는 지워지고 다시 만들어집니다."
	@echo ""
	@echo "== 스키마 최신화 (alembic upgrade head) =="
	cd $(API_DIR) && uv run alembic upgrade head
	@echo "== 시드 적재 =="
	cd $(API_DIR) && uv run python ../../scripts/seed_demo.py

eval: ## 골든셋 평가 실행 (미구현)
	@echo "not yet"
