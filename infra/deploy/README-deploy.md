# CertPilot 데모 배포 절차 (EC2 1대 + RDS)

발표용 데모 환경을 AWS 에 올리는 절차다. PRD §5 의 "Docker Compose(개발) → AWS EC2 1대 +
RDS(데모)" 배포안을 그대로 따른다. 운영 등급 구성이 아니라 **3분 데모를 안정적으로
보여 주기 위한 최소 구성**이다.

> **아직 실제로 배포해 본 적은 없다.** 이 문서와 스크립트는 작성·검증(문법·드라이런)까지만
> 되어 있고, 실제 AWS 자격증명으로 스택을 올린 기록은 없다. 처음 배포할 때는 아래 절차를
> 그대로 따라가면서 막히는 지점을 이 문서에 되먹인다.

---

## 0. 전체 그림

```
                 인터넷
                   │  HTTPS(443)
          ┌────────▼────────┐
          │  EC2 t3.small   │  ← 보안그룹: 443/80 은 전체, 22 는 내 IP 만
          │  ┌───────────┐  │
          │  │  Nginx    │  │  /      → web:3000
          │  │ (또는 ALB)│  │  /api/  → api:8000
          │  └─────┬─────┘  │
          │   docker compose│
          │   web · api ·   │
          │   worker · beat │
          │   redis         │
          └───┬─────────┬───┘
              │         │
     ┌────────▼──┐  ┌───▼──────────┐
     │ RDS       │  │ S3           │
     │ PG16 +    │  │ 원문·산출물  │
     │ pgvector  │  │ + 백업 버킷  │
     └───────────┘  └──────────────┘
```

컨테이너는 `infra/deploy/docker-compose.prod.yml` 이 정의한다.

| 서비스 | 역할 | 포트 |
| --- | --- | --- |
| `api` | FastAPI (uvicorn) | 127.0.0.1:8000 |
| `web` | Next.js (`next start`) | 127.0.0.1:3000 |
| `worker` | Celery 워커 — 인제스트·모의심사·수집 | - |
| `beat` | Celery 비트 — 일 1회 증적 수집 스케줄 | - |
| `redis` | Celery 브로커·결과 백엔드 | 내부 전용 |
| `postgres` | (선택) RDS 대신 쓸 때만. `--profile local-db` | 내부 전용 |

api·web 포트를 루프백에만 여는 이유는, TLS 종료와 라우팅을 앞단 리버스 프록시가
맡기 때문이다. 컨테이너 포트를 인터넷에 직접 열지 않는다.

---

## 1. AWS 리소스 준비

### 1.1 VPC·보안그룹

기본 VPC 를 그대로 쓴다. 보안그룹 두 개를 만든다.

**`certpilot-ec2-sg`** (EC2 에 붙인다)

| 방향 | 프로토콜 | 포트 | 소스 | 이유 |
| --- | --- | --- | --- | --- |
| 인바운드 | TCP | 443 | 0.0.0.0/0 | 데모 접속(HTTPS) |
| 인바운드 | TCP | 80 | 0.0.0.0/0 | Let's Encrypt 인증 + 443 리다이렉트 |
| 인바운드 | TCP | 22 | **내 사무실/집 IP/32** | 관리용 SSH. 절대 전체 개방하지 않는다 |
| 아웃바운드 | 전체 | - | 0.0.0.0/0 | S3·Anthropic API·패키지 저장소 |

SSH 는 가능하면 열지 말고 **AWS Systems Manager Session Manager** 를 쓰는 편이 낫다
(인바운드 22 를 아예 닫을 수 있다).

**`certpilot-rds-sg`** (RDS 에 붙인다)

| 방향 | 프로토콜 | 포트 | 소스 |
| --- | --- | --- | --- |
| 인바운드 | TCP | 5432 | **`certpilot-ec2-sg`** (CIDR 이 아니라 보안그룹 참조) |

RDS 는 퍼블릭 액세스를 **끈다**. EC2 를 거치지 않으면 접근할 수 없어야 한다.

### 1.2 RDS (PostgreSQL 16)

- 엔진: PostgreSQL 16, 인스턴스 `db.t4g.micro`(데모 기준), 스토리지 20GB gp3
- 퍼블릭 액세스 **아니오**, 보안그룹 `certpilot-rds-sg`
- 자동 백업 보존 7일, 저장 암호화 **켬**
- 생성 후 pgvector 확장을 켠다. EC2 에서:

```bash
psql "postgresql://certpilot:<암호>@<엔드포인트>:5432/certpilot" \
  -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

> 마이그레이션(`alembic upgrade head`)이 pgvector 타입을 쓰므로, 확장을 먼저 켜지
> 않으면 배포 3단계에서 실패한다.

### 1.3 S3 버킷 2개

| 버킷 | 용도 | 설정 |
| --- | --- | --- |
| `certpilot-demo-<접미사>` | 원문 문서·증적·산출물 | 퍼블릭 액세스 전면 차단, SSE-S3, 버전 관리 켬 |
| `certpilot-demo-backup-<접미사>` | pg_dump 백업 | 퍼블릭 액세스 전면 차단, SSE-S3, 수명주기 규칙 35일 만료 |

> **알려진 제약**: `apps/api/app/services/storage.py` 가 S3 리전을 `us-east-1` 로
> 고정한다. 지금은 버킷을 **us-east-1 에 만들고** `.env.prod` 의 `S3_ENDPOINT` 를
> `https://s3.us-east-1.amazonaws.com` 으로 둔다. 서울 리전 버킷을 쓰려면 설정에
> `S3_REGION` 을 추가하는 작업이 먼저 필요하다.

백업 버킷의 수명주기 규칙(35일)은 `backup.sh` 의 30일 삭제와 이중 안전장치다.
스크립트가 안 돌아도 쓰레기가 무한히 쌓이지 않는다.

### 1.4 IAM

- **EC2 인스턴스 프로파일**: `AmazonSSMManagedInstanceCore`(Session Manager) +
  백업 버킷에 대한 `s3:PutObject`/`s3:ListBucket`/`s3:DeleteObject` 인라인 정책.
- **애플리케이션용 IAM 사용자**: 현재 코드가 액세스 키를 직접 받으므로
  (`S3_ACCESS_KEY`/`S3_SECRET_KEY`), 데모용 사용자를 하나 만들고 문서 버킷에만
  권한을 준다. 키는 `.env.prod` 에만 두고 90일 안에 폐기한다.

### 1.5 고객 계정의 읽기 전용 역할 (증적 수집용)

증적 커넥터가 붙을 **고객(또는 샌드박스) AWS 계정**에는 리포에 이미 있는
CloudFormation 템플릿으로 읽기 전용 역할을 만든다.

```bash
aws cloudformation deploy \
  --template-file infra/cloudformation/certpilot-readonly-role.yaml \
  --stack-name certpilot-readonly \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
      CertPilotAccountId=<CertPilot 계정 ID> \
      ExternalId=<커넥터별 외부 ID>
```

출력(`Outputs`)의 `RoleArn` 과 외부 ID 를 CertPilot 커넥터 등록 화면에 넣는다.
이 역할은 조회(Describe/List/Get) 권한만 가진다(PRD §10, CLAUDE.md 절대 규칙 4).

---

## 2. EC2 준비

```bash
# Amazon Linux 2023 기준
sudo dnf update -y
sudo dnf install -y docker git
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"   # 다시 로그인해야 적용된다

# docker compose v2 플러그인
sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo curl -fsSL \
  https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

# 타임존과 로그 디렉터리
sudo timedatectl set-timezone Asia/Seoul
sudo mkdir -p /var/log/certpilot && sudo chown "$USER" /var/log/certpilot

# 소스
sudo mkdir -p /srv && sudo chown "$USER" /srv
git clone <리포 URL> /srv/certpilot
```

t3.small(2GB)은 Next.js 빌드 중에 메모리가 모자랄 수 있다. 스왑을 2GB 잡아 둔다.

```bash
sudo dd if=/dev/zero of=/swapfile bs=1M count=2048
sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

---

## 3. 환경 변수

```bash
cd /srv/certpilot/infra/deploy
cp .env.prod.example .env.prod
chmod 600 .env.prod
$EDITOR .env.prod          # CHANGE_ME 를 전부 채운다
```

`deploy.sh` 는 `.env.prod` 권한이 600/400 이 아니거나 `CHANGE_ME` 가 남아 있으면
배포를 거부한다.

비밀 값을 EC2 디스크에 두는 게 걸리면 SSM Parameter Store 를 쓴다.

```bash
aws ssm put-parameter --name /certpilot/prod/SESSION_SECRET \
  --type SecureString --value "$(openssl rand -base64 48)"

# 배포 직전에 내려받아 .env.prod 를 만든다
aws ssm get-parameters-by-path --path /certpilot/prod --with-decryption \
  --query 'Parameters[].[Name,Value]' --output text \
  | awk '{n=$1; sub(/.*\//,"",n); print n"="$2}' > .env.prod
chmod 600 .env.prod
```

키 생성 명령:

| 변수 | 생성 |
| --- | --- |
| `SESSION_SECRET` | `openssl rand -base64 48` |
| `CONNECTOR_ENCRYPTION_KEY` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |

`CONNECTOR_ENCRYPTION_KEY` 를 바꾸면 저장된 AWS 커넥터를 복호화할 수 없다. 한 번
정하면 바꾸지 말고, 바꿔야 하면 커넥터를 다시 등록한다.

---

## 4. 배포

```bash
cd /srv/certpilot/infra/deploy
./deploy.sh --pull            # git pull → 빌드 → 마이그레이션 → 재기동 → 헬스체크
./deploy.sh --pull --seed-demo  # 데모 시드까지(기존 '데모핀테크' 데이터는 지워진다)
```

`deploy.sh` 가 하는 일은 순서대로 다음과 같다.

1. 사전 점검 — docker/compose 존재, `.env.prod` 권한과 `CHANGE_ME` 확인
2. `git pull --ff-only` (`--pull` 일 때만)
3. `docker compose pull` (베이스 이미지) + `docker compose build` (api·web)
4. `redis` 기동 후 `alembic upgrade head` — **여기서 실패하면 재기동하지 않는다**
5. `docker compose up -d` + `/health` 90초 대기

RDS 없이 한 대에서 전부 돌릴 때는 `--local-db` 를 붙이고 `.env.prod` 의
`DATABASE_URL` 호스트를 `postgres` 로 바꾼다.

### 리버스 프록시 (Nginx 예시)

```nginx
server {
    listen 443 ssl http2;
    server_name certpilot-demo.example.com;

    ssl_certificate     /etc/letsencrypt/live/certpilot-demo.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/certpilot-demo.example.com/privkey.pem;

    # 문서 업로드 여유. 기본 1MB 로는 PDF 가 막힌다.
    client_max_body_size 50m;

    location /api/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

`NEXT_PUBLIC_API_URL` 은 브라우저가 보는 주소(`https://.../api`)여야 하고,
**빌드 시점에 번들에 박힌다.** 주소를 바꾸면 `deploy.sh` 로 web 을 다시 빌드한다.
같은 도메인 뒤에 두더라도 API 의 `WEB_ORIGINS` 에 그 도메인을 넣어야 CORS 가 열린다.

세션 쿠키가 `Secure` 로 나가므로(`SESSION_COOKIE_SECURE=true`) HTTPS 없이는
로그인이 되지 않는다. 인증서를 먼저 붙인다.

---

## 5. 백업

`backup.sh` 가 pg_dump → S3 업로드 → 30일 초과분 삭제를 한 번에 한다.

```bash
cd /srv/certpilot/infra/deploy
./backup.sh --dry-run     # 무엇을 할지 확인
./backup.sh               # 실제 백업
```

cron 등록은 `crontab.example` 을 그대로 쓴다(매일 04:10).

```bash
crontab /srv/certpilot/infra/deploy/crontab.example
crontab -l
```

복원:

```bash
aws s3 cp s3://<백업버킷>/postgres/certpilot-<타임스탬프>.dump ./restore.dump
docker run --rm --network host -v "$PWD:/backup" postgres:16-alpine \
  pg_restore --clean --if-exists --no-owner \
    -d "postgresql://certpilot:<암호>@<RDS 엔드포인트>:5432/certpilot" \
    /backup/restore.dump
```

RDS 자동 백업(7일)과 이 논리 백업(30일)은 목적이 다르다. 전자는 인스턴스 장애 복구,
후자는 실수로 지운 데이터를 골라 되살리기 위한 것이다.

---

## 6. 운영 중 확인

```bash
cd /srv/certpilot/infra/deploy
COMPOSE="docker compose -f docker-compose.prod.yml --env-file .env.prod"

$COMPOSE ps                       # 컨테이너 상태
$COMPOSE logs -f api              # API 로그
$COMPOSE logs -f worker           # 잡 처리 로그
curl -fsS http://127.0.0.1:8000/health

# 데모 시드 다시 적재
$COMPOSE run --rm --no-deps api python /srv/certpilot/scripts/seed_demo.py

# 골든셋 평가 실행
$COMPOSE run --rm --no-deps api python /srv/certpilot/scripts/eval_run.py
```

롤백은 `.env.prod` 의 `IMAGE_TAG` 를 이전 태그로 바꾸고 `./deploy.sh` 를 다시 돌린다.
DB 스키마가 함께 내려가야 하면 `alembic downgrade` 를 먼저 수동으로 확인한다.

---

## 7. 데모 전 점검표

- [ ] `https://<도메인>/health` 가 `{"status":"ok"}`
- [ ] 데모 계정 4개로 로그인된다 (`make demo` 출력의 계정 표)
- [ ] 문서 12개가 "파싱 완료", 모의심사 판정 101개가 보인다
- [ ] 심사원 계정으로 초안 승인 전에는 다운로드가 막힌다 (D5)
- [ ] 대시보드에 변경 감지 알림과 사후심사 D-day 가 보인다
- [ ] `./backup.sh --dry-run` 이 통과한다
- [ ] EC2 보안그룹의 22번 포트가 내 IP 로만 열려 있다
- [ ] `.env.prod` 권한이 600 이고 git 에 올라가지 않았다 (`git status`)

---

## 8. 비용 감각 (ap-northeast-2, 데모 한 달 기준 대략)

| 항목 | 대략 |
| --- | --- |
| EC2 t3.small | 월 20 USD 내외 |
| RDS db.t4g.micro + 20GB | 월 20 USD 내외 |
| S3 + 데이터 전송 | 월 1 USD 미만 |
| Anthropic API | 모의심사 1회 상한 5 USD (`app/workers/assess.py`) |

발표가 끝나면 EC2·RDS 를 지우거나 정지한다. 스냅샷만 남겨도 데모는 다시 살릴 수 있다.
