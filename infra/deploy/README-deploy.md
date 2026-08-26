# CertPilot 배포 절차 (오라클 클라우드 VM 우선 / AWS EC2 대안)

발표용 데모 환경을 올리는 절차다. 운영 등급 구성이 아니라 **3분 데모를 안정적으로
보여 주기 위한 최소 구성**이다.

배포 스택 자체는 클라우드에 묶여 있지 않다. `docker` + `compose v2` 가 있는 리눅스
VM 한 대면 어디서든 같은 명령으로 뜬다. AWS 관리형 서비스(RDS·S3)는 **있으면 쓰고,
없으면 컨테이너로 대신한다**(compose 프로파일 `local-db` / `local-storage`).

> **아직 실제로 배포해 본 적은 없다.** 이 문서와 스크립트는 작성·검증(문법·렌더·드라이런)
> 까지만 되어 있고, 실제 클라우드 자격증명으로 스택을 올린 기록은 없다. 처음 배포할 때는
> 아래 절차를 그대로 따라가면서 막히는 지점을 이 문서에 되먹인다.

---

## 0. 배포 경로 고르기

| | **A. 오라클 클라우드 VM** (§1, 권장 임시 경로) | **B. AWS EC2 + RDS + S3** (§2, 대안) |
| --- | --- | --- |
| 비용 | Always Free 한도 안이면 0원 | 월 40 USD 내외 |
| DB | 컨테이너 (`local-db` 프로파일) | RDS PostgreSQL 16 |
| 오브젝트 스토리지 | MinIO 컨테이너 (`local-storage`) 또는 오라클 Object Storage | S3 |
| CPU | Ampere A1 = **arm64** (§1.2 확인 필요) | t3.small = x86_64 |
| 접속 | `http://<공인IP>:3000` (평문) 또는 Nginx+도메인 | `https://<도메인>` |
| 데이터 수명 | VM 을 지우면 같이 사라진다 | RDS·S3 에 남는다 |

**지금 필요한 건 A 다.** 발표가 끝나면 인스턴스를 지운다.

두 경로가 공유하는 것: §3 환경 변수, §4 배포, §5 백업, §6 운영, §7 점검표.

컨테이너 구성은 `infra/deploy/docker-compose.prod.yml` 이 정의한다.

| 서비스 | 역할 | 포트 | 프로파일 |
| --- | --- | --- | --- |
| `api` | FastAPI (uvicorn) | `${API_BIND}:${API_PORT}` (기본 8000) | 항상 |
| `web` | Next.js (`next start`) | `${WEB_BIND}:${WEB_PORT}` (기본 3000) | 항상 |
| `worker` | Celery 워커 — 인제스트·모의심사·수집 | - | 항상 |
| `beat` | Celery 비트 — 일 1회 증적 수집 스케줄 | - | 항상 |
| `redis` | Celery 브로커·결과 백엔드 | 내부 전용 | 항상 |
| `postgres` | PostgreSQL 16 + pgvector | 내부 전용 | `local-db` |
| `minio` | S3 호환 오브젝트 스토리지 | 콘솔만 127.0.0.1:9001 | `local-storage` |

`API_BIND` / `WEB_BIND` 는 기본값이 `127.0.0.1` 이다. 앞단에 리버스 프록시를 두는
구성에서는 그대로 두고, 공인 IP 로 바로 접속시킬 때만 `0.0.0.0` 으로 바꾼다(§1.5).

`API_PORT` / `WEB_PORT` 는 **호스트에 게시할 포트**다(컨테이너 안쪽은 항상 8000/3000).
그 서버에 다른 서비스가 이미 8000·3000 을 쓰고 있으면 이 값만 겹치지 않게 바꾼다
(§4 "GitHub Actions CD" 의 서버가 그렇다 — 8010/3010 을 쓴다).

---

## 1. 오라클 클라우드 VM 배포 (권장 임시 경로)

### 1.1 인스턴스 만들기

OCI 콘솔 > Compute > Instances > Create instance.

| 항목 | 값 | 이유 |
| --- | --- | --- |
| Shape | **VM.Standard.A1.Flex**, 4 OCPU / 24 GB | Always Free 최대치. Next.js 빌드와 워커를 같이 돌리려면 이 정도가 편하다 |
| Image | Ubuntu 24.04 (또는 Oracle Linux 9) | 아래 명령은 Ubuntu 기준. OL 은 `dnf`/`firewalld` 로 바꾼다 |
| 부트 볼륨 | 50 GB 이상 | 이미지 빌드 캐시가 금방 쌓인다 |
| SSH 키 | 공개키 등록 | 비밀번호 로그인은 없다 |
| 퍼블릭 IP | 할당 | 데모 접속 주소가 된다 |

> A1(Ampere)은 **arm64** 다. Always Free 의 x86 형상(VM.Standard.E2.1.Micro, 1 OCPU /
> 1 GB)은 Next.js 빌드에서 메모리가 모자란다. 굳이 x86 을 써야 한다면 스왑을 4 GB
> 잡고(§1.4) 빌드는 다른 곳에서 해서 이미지를 옮기는 편이 낫다.

### 1.2 arm64(Ampere) 멀티아치 호환 확인

이미지는 **빌드하는 VM 의 아키텍처로** 만들어진다. arm64 VM 에서는 베이스 이미지에
`linux/arm64` 매니페스트가 있어야 `docker compose pull` 이 통과한다. 배포 전에 한 번
확인한다.

```bash
for image in \
  python:3.12-slim-bookworm \
  node:20-alpine \
  redis:7 \
  pgvector/pgvector:pg16 \
  minio/minio \
  postgres:16-alpine        # backup.sh 가 pg_dump 용으로 쓴다
do
  printf '%-30s ' "$image"
  docker buildx imagetools inspect "$image" 2>/dev/null \
    | grep -i 'platform' | tr -d ' ' | paste -sd' ' - \
    || echo '확인 실패'
done
```

출력에 `linux/arm64` 가 보이면 된다(`linux/arm64/v8` 도 같다). 위 6개는 모두 arm64
매니페스트를 제공한다. 하나라도 빠졌다면 그 서비스만 x86 VM 으로 옮기거나 대체
이미지를 찾아야 한다.

현재 아키텍처를 다시 확인하려면:

```bash
uname -m                                    # aarch64 면 arm64 다
docker info --format '{{.Architecture}}'
```

`deploy.sh` 도 시작할 때 아키텍처를 찍고, arm64 면 경고를 남긴다.

빌드에서 막히는 흔한 지점 두 가지.

- **파이썬 휠**: `psycopg`·`lxml` 같은 C 확장은 arm64 휠이 없으면 소스 빌드로 넘어간다.
  빌드가 몇 분 길어질 뿐 실패는 아니지만, 컴파일러 오류가 나면 로그의 첫 오류를 본다.
- **이미지 이식 금지**: arm64 VM 에서 만든 `certpilot-api` / `certpilot-web` 이미지는
  x86_64 서버에서 `exec format error` 로 죽는다. 옮겨 쓸 계획이면 `docker buildx build
  --platform linux/amd64,linux/arm64` 로 따로 만든다.

### 1.3 포트 열기 — VCN 보안 목록 **과** OS 방화벽

오라클은 **두 겹**으로 막는다. 한쪽만 열면 접속이 안 되는데 원인이 잘 안 보이므로
둘 다 확인한다.

**(1) VCN 보안 목록(또는 NSG)** — 콘솔 > Networking > VCN > Subnet > Security List >
Add Ingress Rules.

| Source CIDR | 프로토콜 | 목적 포트 | 이유 |
| --- | --- | --- | --- |
| `<내 IP>/32` | TCP | 22 | 관리용 SSH. 전체 개방하지 않는다 |
| `0.0.0.0/0` | TCP | 3000 | web (Next.js) — 프록시 없이 직접 열 때 |
| `0.0.0.0/0` | TCP | 8000 | api (FastAPI) — 브라우저가 직접 호출한다 |

Nginx 를 앞에 두는 구성이면 3000·8000 대신 **80·443** 만 연다(§1.7).

**(2) OS 방화벽** — OCI 리눅스 이미지는 22 를 뺀 나머지를 기본 차단한다.

Ubuntu (ufw):

```bash
sudo ufw allow 3000/tcp
sudo ufw allow 8000/tcp
sudo ufw status verbose        # inactive 면 아래 iptables 규칙이 실제 차단자다
```

OCI Ubuntu 이미지는 ufw 가 꺼진 채로 `iptables` 규칙이 미리 들어 있는 경우가 많다.
그때는 규칙을 직접 넣고 저장한다.

```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 3000 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 8000 -j ACCEPT
sudo netfilter-persistent save        # 재부팅 후에도 유지
```

Oracle Linux 9 (firewalld):

```bash
sudo firewall-cmd --permanent --add-port=3000/tcp
sudo firewall-cmd --permanent --add-port=8000/tcp
sudo firewall-cmd --reload
sudo firewall-cmd --list-ports
```

확인은 **VM 밖에서** 한다(안에서 curl 하면 방화벽을 안 거친다).

```bash
curl -fsS "http://<공인IP>:8000/health"     # {"status":"ok"}
```

> 도커가 포트를 게시하면 `nat` 테이블에 규칙을 직접 넣기 때문에 ufw 설정을 건너뛰는
> 일이 있다. 즉 **ufw 로 막았다고 안심하지 말고** 위 curl 로 실제 노출 상태를 본다.
> `API_BIND=127.0.0.1` 이면 애초에 게시되지 않으므로 그게 가장 확실한 차단이다.

### 1.4 호스트 준비

```bash
# Ubuntu 24.04 기준
sudo apt-get update
sudo apt-get install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$USER"      # 다시 로그인해야 적용된다

# 타임존, 로그·백업 디렉터리
sudo timedatectl set-timezone Asia/Seoul
sudo mkdir -p /var/log/certpilot /var/backups/certpilot
sudo chown "$USER" /var/log/certpilot /var/backups/certpilot

# 소스
sudo mkdir -p /srv && sudo chown "$USER" /srv
git clone <리포 URL> /srv/certpilot
```

24 GB 형상이면 스왑은 필요 없다. 1~2 GB 형상을 쓴다면 빌드 전에 잡아 둔다.

```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### 1.5 `.env.prod` — 오라클 VM 용 핵심 값

```bash
cd /srv/certpilot/infra/deploy
cp .env.prod.example .env.prod
chmod 600 .env.prod
$EDITOR .env.prod
```

공인 IP 로 바로 접속시키는 구성이라면 다음 값이 서로 **정확히** 맞아야 한다.
(`<공인IP>` 는 실제 주소로 바꾼다. 포트까지 포함해 한 글자도 다르면 안 된다.)

```dotenv
# 관리형 서비스 없이 이 VM 안에서 전부 돌린다
CERTPILOT_PROFILES=local-db,local-storage

# DB 는 컨테이너. 호스트 이름이 곧 컨테이너 이름이다
DATABASE_URL=postgresql+psycopg://certpilot:<암호>@postgres:5432/certpilot
POSTGRES_USER=certpilot
POSTGRES_PASSWORD=<암호>            # 위 URL 과 반드시 동일
POSTGRES_DB=certpilot

# 오브젝트 스토리지도 컨테이너 (§1.6)
S3_ENDPOINT=http://minio:9000
S3_BUCKET=certpilot
S3_ACCESS_KEY=certpilot             # MinIO 루트 사용자 (3자 이상)
S3_SECRET_KEY=<8자 이상 비밀번호>    # MinIO 루트 비밀번호
S3_REGION=us-east-1                 # MinIO 는 아무 값이나 통과

# 브라우저가 보는 주소. NEXT_PUBLIC_* 는 빌드 시점에 번들에 박힌다
NEXT_PUBLIC_API_URL=http://<공인IP>:8000
WEB_ORIGINS=http://<공인IP>:3000
# 운영·외부 공개에서는 정규식 허용을 끈다(빈 값). 명시 목록만 쓴다
WEB_ORIGIN_REGEX=

# 평문 http 다 → Secure 쿠키를 끈다. true 면 브라우저가 세션 쿠키를 버려 로그인이 안 된다
SESSION_COOKIE_SECURE=false

# 컨테이너 포트를 공인 IP 에 게시한다 (기본값 127.0.0.1 이면 외부에서 안 보인다)
API_BIND=0.0.0.0
WEB_BIND=0.0.0.0

CELERY_CONCURRENCY=4                # 4 OCPU / 24 GB 기준
```

세 값의 관계가 이 구성에서 가장 자주 틀리는 부분이다.

- `NEXT_PUBLIC_API_URL` — 브라우저가 API 를 **호출할** 주소. 틀리면 화면은 뜨는데
  모든 요청이 실패한다. 바꾸면 web 이미지를 다시 빌드해야 한다(`deploy.sh` 가 한다).
- `WEB_ORIGINS` — API 가 CORS 로 **허용할** 출처. 브라우저 주소창의 스킴·호스트·포트와
  똑같아야 한다. 틀리면 브라우저 콘솔에 CORS 오류가 뜬다.
- `WEB_ORIGIN_REGEX` — 코드 기본값은 사설망(`192.168.x.x` 등)을 허용하는 개발 편의
  기능이다. 공인 IP 로 여는 배포에서는 **빈 값으로 꺼 둔다**. 오라클 VM 의 공인 IP 는
  이 정규식에 걸리지 않으므로 켜 둬도 도움이 되지 않고 허용 범위만 넓힌다.

`SESSION_COOKIE_SECURE` 를 `false` 로 두는 건 **평문 http 데모라서** 하는 타협이다.
로그인 세션이 네트워크에 그대로 흐른다. 데모 계정 말고 실제 자격증명을 넣지 않는다.
외부에 오래 열어 둘 거라면 §1.7 로 TLS 를 붙이고 `true` 로 되돌린다.

### 1.6 오브젝트 스토리지 두 가지 선택지

원문 문서·증적 스냅샷·산출물이 저장되는 곳이다. DB 에는 마스킹된 청크만 들어간다.

#### (a) MinIO 컨테이너 — `local-storage` 프로파일

가장 빠른 길이다. 외부 계정도, 키 발급도 필요 없다.

```dotenv
CERTPILOT_PROFILES=local-db,local-storage
S3_ENDPOINT=http://minio:9000
S3_BUCKET=certpilot
S3_ACCESS_KEY=certpilot
S3_SECRET_KEY=<8자 이상>
S3_REGION=us-east-1
```

`S3_ACCESS_KEY` / `S3_SECRET_KEY` 가 그대로 MinIO 의 루트 자격증명이 된다
(compose 가 `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` 로 넘긴다). 값이 어긋나면
업로드가 403 으로 떨어진다. MinIO 제약상 사용자 3자 이상, 비밀번호 8자 이상이다.

버킷은 api 가 첫 업로드 때 `ensure_bucket()` 으로 만든다. 미리 만들 필요는 없다.

S3 API 포트(9000)는 호스트에 게시하지 않는다. api·worker 가 compose 네트워크 안에서
`minio` 이름으로 붙기 때문이다. 웹 콘솔만 루프백 9001 에 열려 있으니 볼 일이 있으면
SSH 터널을 쓴다.

```bash
ssh -L 9001:127.0.0.1:9001 ubuntu@<공인IP>
# 브라우저에서 http://127.0.0.1:9001 (로그인은 위 키/시크릿)
```

> **데이터가 VM 에만 있다.** `minio_data` 볼륨이 유일한 사본이므로, 인스턴스를 지우면
> 원문 문서도 사라진다. `backup.sh` 는 DB 만 뜬다 — 볼륨 백업은 `crontab.example` 의
> 주간 tar 항목을 참고한다.

#### (b) 오라클 Object Storage (S3 호환 API)

VM 을 지워도 데이터를 남기고 싶을 때. 코드 변경 없이 붙는다
(`app/services/storage.py` 가 이미 path-style 주소 + SigV4 를 쓴다).

1. **버킷 생성** — 콘솔 > Storage > Buckets > Create Bucket. 이름 `certpilot-demo`,
   가시성 **Private**. (`ensure_bucket()` 이 만들어 주기도 하지만, 권한 문제를 먼저
   드러내기 위해 콘솔에서 만들어 두는 편이 낫다.)
2. **네임스페이스 확인** — 콘솔의 버킷 상세에 보이거나, CLI 로:
   ```bash
   oci os ns get --query 'data' --raw-output
   ```
3. **고객 비밀 키(Customer Secret Key) 발급** — 콘솔 > Identity > Users > 내 사용자 >
   Resources > **Customer Secret Keys** > Generate Secret Key. 여기서 나오는
   Access Key / Secret Key 가 S3 호환 API 의 자격증명이다. **일반 API 서명 키(PEM)가
   아니다.** 시크릿은 그때 한 번만 보인다.
4. `.env.prod` 에 채운다.

```dotenv
CERTPILOT_PROFILES=local-db          # local-storage 는 빼야 MinIO 가 안 뜬다
S3_ENDPOINT=https://<네임스페이스>.compat.objectstorage.<리전>.oraclecloud.com
S3_BUCKET=certpilot-demo
S3_ACCESS_KEY=<고객 비밀 키의 Access Key>
S3_SECRET_KEY=<고객 비밀 키의 Secret>
S3_REGION=<리전>                      # 버킷 리전과 반드시 일치
```

리전 식별자는 엔드포인트에 들어간 것과 같다(춘천 `ap-chuncheon-1`, 서울 `ap-seoul-1`).
`S3_REGION` 은 SigV4 **서명**에만 쓰이지만, 값이 다르면 요청이 통째로 거부된다
(`AuthorizationHeaderMalformed` 또는 `SignatureDoesNotMatch`). 엔드포인트의 리전과
글자 그대로 맞춘다.

붙었는지 확인:

```bash
cd /srv/certpilot/infra/deploy
docker compose -f docker-compose.prod.yml --env-file .env.prod \
  run --rm --no-deps api python -c "
from app.services.storage import get_storage
s = get_storage()
s.ensure_bucket()
s.put_object('healthcheck.txt', b'ok', 'text/plain')
print('OK:', s.get_object('healthcheck.txt'))
s.delete_object('healthcheck.txt')
"
```

### 1.7 (선택) Nginx + TLS 로 올리기

평문 http 가 부담스럽거나 데모를 며칠 열어 둬야 하면 앞에 Nginx 를 둔다. 이때
`API_BIND`/`WEB_BIND` 를 `127.0.0.1` 로 되돌리고, VCN·OS 방화벽에서 3000·8000 을 닫고
80·443 만 연다.

도메인이 없으면 `nip.io` 같은 와일드카드 DNS 로 Let's Encrypt 인증서를 받을 수 있다
(`<공인IP>.nip.io` → `<공인IP>`). 설정 예시는 §2.5 의 Nginx 블록과 같고, 바뀌는 값은
이것뿐이다.

```dotenv
NEXT_PUBLIC_API_URL=https://<공인IP>.nip.io/api
WEB_ORIGINS=https://<공인IP>.nip.io
SESSION_COOKIE_SECURE=true
API_BIND=127.0.0.1
WEB_BIND=127.0.0.1
```

### 1.8 배포하고 확인

```bash
cd /srv/certpilot/infra/deploy
./deploy.sh --seed-demo         # .env.prod 의 CERTPILOT_PROFILES 를 읽는다
```

프로파일을 그때그때 바꾸려면 CLI 나 셸 환경 변수로 덮어쓴다(§4).

```bash
./deploy.sh --profiles local-db,local-storage
CERTPILOT_PROFILES=local-db ./deploy.sh
```

VM 밖에서:

```bash
curl -fsS "http://<공인IP>:8000/health"    # {"status":"ok"}
open "http://<공인IP>:3000"                # 브라우저에서 로그인
```

로그인이 안 되면 순서대로 본다. ① 브라우저 콘솔의 CORS 오류 → `WEB_ORIGINS`,
② 요청이 아예 안 나감 → `NEXT_PUBLIC_API_URL`(web 재빌드 필요), ③ 로그인은 되는데
새로고침하면 풀림 → `SESSION_COOKIE_SECURE` 가 `true` 인데 http 로 접속 중이다.

---

## 2. AWS EC2 + RDS 배포 (대안)

관리형 서비스를 쓸 수 있을 때의 구성이다. PRD §5 의 "Docker Compose(개발) → AWS EC2
1대 + RDS(데모)" 배포안을 따른다.

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

이 구성에서는 compose 프로파일을 쓰지 않는다(`CERTPILOT_PROFILES` 를 빈 값으로).

### 2.1 VPC·보안그룹

기본 VPC 를 그대로 쓴다. 보안그룹 두 개를 만든다.

**`certpilot-ec2-sg`** (EC2 에 붙인다)

| 방향 | 프로토콜 | 포트 | 소스 | 이유 |
| --- | --- | --- | --- | --- |
| 인바운드 | TCP | 443 | 0.0.0.0/0 | 데모 접속(HTTPS) |
| 인바운드 | TCP | 80 | 0.0.0.0/0 | Let's Encrypt 인증 + 443 리다이렉트 |
| 인바운드 | TCP | 22 | **내 사무실/집 IP/32** | 관리용 SSH. 절대 전체 개방하지 않는다 |
| 아웃바운드 | 전체 | - | 0.0.0.0/0 | S3·LLM API·패키지 저장소 |

SSH 는 가능하면 열지 말고 **AWS Systems Manager Session Manager** 를 쓰는 편이 낫다
(인바운드 22 를 아예 닫을 수 있다).

**`certpilot-rds-sg`** (RDS 에 붙인다)

| 방향 | 프로토콜 | 포트 | 소스 |
| --- | --- | --- | --- |
| 인바운드 | TCP | 5432 | **`certpilot-ec2-sg`** (CIDR 이 아니라 보안그룹 참조) |

RDS 는 퍼블릭 액세스를 **끈다**. EC2 를 거치지 않으면 접근할 수 없어야 한다.

### 2.2 RDS (PostgreSQL 16)

- 엔진: PostgreSQL 16, 인스턴스 `db.t4g.micro`(데모 기준), 스토리지 20GB gp3
- 퍼블릭 액세스 **아니오**, 보안그룹 `certpilot-rds-sg`
- 자동 백업 보존 7일, 저장 암호화 **켬**
- 생성 후 pgvector 확장을 켠다. EC2 에서:

```bash
psql "postgresql://certpilot:<암호>@<엔드포인트>:5432/certpilot" \
  -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

> 마이그레이션(`alembic upgrade head`)이 pgvector 타입을 쓰므로, 확장을 먼저 켜지
> 않으면 배포 4단계에서 실패한다. `local-db` 프로파일의 컨테이너 이미지
> (`pgvector/pgvector:pg16`)는 확장이 이미 들어 있어 이 단계가 필요 없다.

### 2.3 S3 버킷 2개

| 버킷 | 용도 | 설정 |
| --- | --- | --- |
| `certpilot-demo-<접미사>` | 원문 문서·증적·산출물 | 퍼블릭 액세스 전면 차단, SSE-S3, 버전 관리 켬 |
| `certpilot-demo-backup-<접미사>` | pg_dump 백업 | 퍼블릭 액세스 전면 차단, SSE-S3, 수명주기 규칙 35일 만료 |

`.env.prod` 에는 버킷 리전을 그대로 적는다. 리전은 SigV4 서명에 쓰이며, 값이 다르면
요청이 거부된다.

```dotenv
S3_ENDPOINT=https://s3.ap-northeast-2.amazonaws.com
S3_BUCKET=certpilot-demo-<접미사>
S3_REGION=ap-northeast-2
```

백업 버킷의 수명주기 규칙(35일)은 `backup.sh` 의 30일 삭제와 이중 안전장치다.
스크립트가 안 돌아도 쓰레기가 무한히 쌓이지 않는다.

### 2.4 IAM

- **EC2 인스턴스 프로파일**: `AmazonSSMManagedInstanceCore`(Session Manager) +
  백업 버킷에 대한 `s3:PutObject`/`s3:ListBucket`/`s3:DeleteObject` 인라인 정책.
- **애플리케이션용 IAM 사용자**: 현재 코드가 액세스 키를 직접 받으므로
  (`S3_ACCESS_KEY`/`S3_SECRET_KEY`), 데모용 사용자를 하나 만들고 문서 버킷에만
  권한을 준다. 키는 `.env.prod` 에만 두고 90일 안에 폐기한다.

### 2.5 EC2 준비와 리버스 프록시

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

sudo timedatectl set-timezone Asia/Seoul
sudo mkdir -p /var/log/certpilot /var/backups/certpilot
sudo chown "$USER" /var/log/certpilot /var/backups/certpilot

sudo mkdir -p /srv && sudo chown "$USER" /srv
git clone <리포 URL> /srv/certpilot
```

t3.small(2GB)은 Next.js 빌드 중에 메모리가 모자랄 수 있다. 스왑을 2GB 잡아 둔다.

```bash
sudo dd if=/dev/zero of=/swapfile bs=1M count=2048
sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

Nginx (오라클 VM 에서 §1.7 로 TLS 를 붙일 때도 같은 설정을 쓴다):

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

프록시를 쓰면 `API_BIND`/`WEB_BIND` 는 기본값(`127.0.0.1`) 그대로 둔다. 컨테이너
포트를 인터넷에 직접 열지 않기 위해서다.

### 2.6 고객 계정의 읽기 전용 역할 (증적 수집용)

증적 커넥터가 붙을 **고객(또는 샌드박스) AWS 계정**에는 리포에 이미 있는
CloudFormation 템플릿으로 읽기 전용 역할을 만든다. 이건 CertPilot 을 어디에 배포하든
(오라클 VM 이어도) 똑같이 필요하다 — 수집 대상이 AWS 계정이기 때문이다.

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

## 3. 환경 변수 (공통)

```bash
cd /srv/certpilot/infra/deploy
cp .env.prod.example .env.prod
chmod 600 .env.prod
$EDITOR .env.prod          # CHANGE_ME 를 전부 채운다
```

`deploy.sh` 는 `.env.prod` 권한이 600/400 이 아니거나 `CHANGE_ME` 가 남아 있으면
배포를 거부한다. **쓰지 않는 항목은 지우거나 빈 값으로 둔다.**

키 생성 명령:

| 변수 | 생성 |
| --- | --- |
| `SESSION_SECRET` | `openssl rand -base64 48` |
| `CONNECTOR_ENCRYPTION_KEY` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |

`CONNECTOR_ENCRYPTION_KEY` 를 바꾸면 저장된 AWS 커넥터를 복호화할 수 없다. 한 번
정하면 바꾸지 말고, 바꿔야 하면 커넥터를 다시 등록한다.

### LLM / 임베딩

| 변수 | 값 | 메모 |
| --- | --- | --- |
| `LLM_PROVIDER` | `auto` \| `openai` \| `anthropic` \| `fake` | `auto` 는 키 있는 것을 골라 쓴다(openai → anthropic → fake) |
| `OPENAI_API_KEY` | 키 | 기본 프로바이더 |
| `OPENAI_MODEL` | `gpt-5.6` | 저가 `gpt-5.6-luna` / 고성능 `gpt-5.5` |
| `ANTHROPIC_API_KEY` | 키 (옵션) | 대체 프로바이더. 안 쓰면 빈 값 |
| `EMBEDDING_PROVIDER` | `auto` \| `openai` \| `hashing` | |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | 1536차원 — 스키마의 `Vector(1536)` 와 맞아야 한다 |

키를 하나도 안 넣으면 결정적 Fake 프로바이더로 부팅은 되지만 판정 품질은 데모용이
아니다. 발표 전에 키를 넣고 모의심사를 한 번 돌려 본다.

**임베딩 프로바이더를 바꾸면 기존 청크 벡터와 비교할 수 없다.** 문서를 다시
인제스트해야 한다(`--seed-demo`).

### 비밀 값을 디스크에 두기 싫을 때 (AWS)

```bash
aws ssm put-parameter --name /certpilot/prod/SESSION_SECRET \
  --type SecureString --value "$(openssl rand -base64 48)"

# 배포 직전에 내려받아 .env.prod 를 만든다
aws ssm get-parameters-by-path --path /certpilot/prod --with-decryption \
  --query 'Parameters[].[Name,Value]' --output text \
  | awk '{n=$1; sub(/.*\//,"",n); print n"="$2}' > .env.prod
chmod 600 .env.prod
```

오라클이라면 OCI Vault 의 시크릿을 `oci secrets secret-bundle get` 으로 같은 식으로
내려받는다.

---

## 4. 배포 (공통)

```bash
cd /srv/certpilot/infra/deploy
./deploy.sh --pull              # git pull → 빌드 → 마이그레이션 → 재기동 → 헬스체크
./deploy.sh --pull --seed-demo  # 데모 시드까지(기존 '데모핀테크' 데이터는 지워진다)
```

`deploy.sh` 가 하는 일은 순서대로 다음과 같다.

1. 사전 점검 — docker/compose 존재, `.env.prod` 권한과 `CHANGE_ME`, 호스트 아키텍처
2. 프로파일 결정 (아래)
3. `git pull --ff-only` (`--pull` 일 때만)
4. `docker compose pull` (베이스 이미지) + `docker compose build` (api·web)
5. `redis` + 활성 프로파일의 컨테이너 기동 후 `alembic upgrade head`
   — **여기서 실패하면 재기동하지 않는다**
6. `docker compose up -d` + `/health` 90초 대기
7. `--seed-demo` 면 데모 시드 적재

### 프로파일 지정 방법

`local-db`(postgres 컨테이너)와 `local-storage`(MinIO 컨테이너)를 켜는 방법은 세 가지고,
**우선순위가 있다. 합쳐지지 않고 덮어쓴다.**

| 우선순위 | 방법 | 예 |
| --- | --- | --- |
| 1 | CLI 옵션 | `./deploy.sh --local-db --local-storage`<br>`./deploy.sh --profiles local-db,local-storage` |
| 2 | 셸 환경 변수 | `CERTPILOT_PROFILES=local-db ./deploy.sh` |
| 3 | `.env.prod` | `CERTPILOT_PROFILES=local-db,local-storage` |

서버마다 조합이 다르면 3번(`.env.prod`)에 적어 고정해 두는 게 편하다. 모르는 이름을
주면 배포가 시작 전에 멈춘다. compose 를 직접 부를 때는 이렇게 한다.

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod \
  --profile local-db --profile local-storage ps
```

> `docker compose config` 로 렌더만 해 볼 때도 같은 디렉터리에 `.env.prod` 가 있어야
> 한다(서비스의 `env_file` 이 그 파일을 가리킨다).

### GitHub Actions CD (리버스 프록시 서버)

`main` 에 push 하면 사람 손 없이 배포되게 하는 구성이다. 대상은 **다른 서비스가 이미
돌고 있는 리눅스 VM 1대**이고, 호스트 Nginx 가 80/443 을 갖고 있다. 그래서 CertPilot 은
겹치지 않는 포트(`web` 3010 / `api` 8010)에 붙고, Nginx 가
`https://certpilot.autoselp.cloud` 로 넘겨준다.

```
main push → CI(ci.yml) 녹색 → CD(cd.yml) 트리거(workflow_run)
          → SSH 로 서버 접속
          → cd /opt/certpilot/app && git fetch && git checkout --detach <sha>
          → infra/deploy/deploy-from-ci.sh <sha>
               └ IMAGE_TAG=<sha> ./deploy.sh
                   ├ 성공 → /opt/certpilot/last_good_sha 갱신, 종료 0
                   └ 실패 → last_good 커밋으로 checkout 후 재배포(롤백), 종료 1
          → 러너에서 https://certpilot.autoselp.cloud/api/health 확인 (10회 × 6초)
```

CD 가 배포하는 커밋은 `workflow_run.head_sha` — **CI 가 실제로 검증한 그 커밋**이다.
`workflow_dispatch` 로 수동 실행하면 선택한 브랜치의 `github.sha` 를 쓴다.

롤백해도 CD 는 빨간불로 끝난다. 서비스는 복구됐지만 배포는 실패한 것이기 때문이다.
로그에 "롤백 성공 / 롤백도 실패" 가 한국어로 남으니 그것으로 상태를 판단한다.

#### 서버 1회 셋업

```bash
# 1. 배포 루트 — CD 는 이 안의 git clone 하나만 쓴다
sudo mkdir -p /opt/certpilot && sudo chown "$USER" /opt/certpilot
git clone https://github.com/YoonJae00/certPilot.git /opt/certpilot/app

# 2. .env.prod — 체크아웃으로 지워지지 않는다(gitignore 대상이라 추적되지 않는다)
cd /opt/certpilot/app/infra/deploy
cp .env.prod.example .env.prod
chmod 600 .env.prod
$EDITOR .env.prod
```

이 서버에서 기본값과 달라야 하는 값들.

```dotenv
API_PORT=8010                 # 8000 은 옆 서비스가 쓰고 있다
WEB_PORT=3010                 # 3000 도 마찬가지
API_BIND=127.0.0.1            # 외부는 Nginx 만 통한다
WEB_BIND=127.0.0.1

NEXT_PUBLIC_API_URL=https://certpilot.autoselp.cloud/api
WEB_ORIGINS=https://certpilot.autoselp.cloud
WEB_ORIGIN_REGEX=             # 공개 배포다 — 사설망 허용 정규식을 끈다
SESSION_COOKIE_SECURE=true    # https 다
```

```bash
# 3. Nginx vhost + 인증서 — 두 단계로 나눈다.
#    처음에는 인증서 파일이 아직 없어서 443 블록째로 넣으면 `nginx -t` 가 실패한다.
sudo mkdir -p /var/www/certbot
sudo cp /opt/certpilot/app/infra/deploy/nginx-certpilot.conf.example \
        /etc/nginx/conf.d/certpilot.conf

# 3-1. 443 server 블록 전체를 주석 처리한 채(80 블록만) 먼저 반영한다
sudo $EDITOR /etc/nginx/conf.d/certpilot.conf
sudo nginx -t && sudo nginx -s reload

# 3-2. 인증서를 발급받는다 (80 블록의 acme-challenge 경로로 검증된다)
sudo certbot certonly --webroot -w /var/www/certbot -d certpilot.autoselp.cloud \
     --deploy-hook "nginx -t && nginx -s reload"

# 3-3. 443 블록 주석을 풀고 다시 반영한다
sudo $EDITOR /etc/nginx/conf.d/certpilot.conf
sudo nginx -t && sudo nginx -s reload
```

> 이 서버의 nginx 는 systemd 유닛이 아니라 별도 프로세스로 떠 있다.
> **`systemctl restart nginx` 를 쓰지 않는다.** 적용은 `nginx -t && nginx -s reload` 로만.

```bash
# 4. CD 가 쓸 SSH 공개키 등록 (러너에서 접속할 키. 새로 만든 전용 키를 권장)
#    로컬에서: ssh-keygen -t ed25519 -C certpilot-cd -f ~/.ssh/certpilot_cd
cat >> ~/.ssh/authorized_keys   # 여기에 certpilot_cd.pub 내용을 붙인다

# 5. 첫 배포는 손으로 한 번 돌려 본다(CD 는 이미 도는 스택을 갱신하는 용도다)
cd /opt/certpilot/app/infra/deploy && ./deploy.sh
```

#### GitHub 쪽 설정

리포지토리 Settings > Environments > **`production`** 을 만들고 secrets 5개를 넣는다.
(environment 로 두면 필요할 때 승인 게이트·브랜치 제한을 붙일 수 있다.)

| 시크릿 | 값 | 만드는 법 |
| --- | --- | --- |
| `SSH_HOST` | 서버 주소 | 공인 IP 또는 호스트명 |
| `SSH_PORT` | SSH 포트 | 보통 `22` |
| `SSH_USER` | 접속 계정 | docker 그룹에 속하고 `/opt/certpilot` 에 쓸 수 있어야 한다 |
| `SSH_PRIVATE_KEY` | 개인키 전문 | `cat ~/.ssh/certpilot_cd` (`-----BEGIN` ~ `-----END` 줄 포함) |
| `SSH_KNOWN_HOSTS` | 서버 호스트 키 | `ssh-keyscan -p <포트> <호스트>` 출력 그대로 |

`SSH_KNOWN_HOSTS` 를 채우는 이유는 `StrictHostKeyChecking` 을 끄지 않기 위해서다.
끄면 중간자 공격에 무방비가 된다. 서버를 재설치해 호스트 키가 바뀌면 이 값도 갱신한다.

CD 는 서드파티 액션을 쓰지 않는다(공급망을 줄인다). SSH 는 워크플로 안에서 셸로 직접
다루고, 작업이 끝나면 개인키 파일을 지운다.

#### 확인과 문제 해결

```bash
curl -fsS https://certpilot.autoselp.cloud/api/health     # {"status":"ok"}
cat /opt/certpilot/last_good_sha                          # 마지막 성공 커밋
cd /opt/certpilot/app && git log -1 --oneline             # 지금 떠 있는 커밋
```

| 증상 | 먼저 볼 곳 |
| --- | --- |
| CD 가 아예 안 돈다 | CI 가 `main` 에서 성공했는지. 다른 브랜치·실패면 배포하지 않는다 |
| SSH 단계에서 멈춘다 | `SSH_KNOWN_HOSTS` 가 현재 호스트 키와 같은지, 공개키가 `authorized_keys` 에 있는지 |
| `deploy.sh` 가 `.env.prod` 로 실패 | 체크아웃 후에도 `infra/deploy/.env.prod` 가 있는지, 권한이 600 인지 |
| 배포는 됐는데 헬스체크만 실패 | Nginx `proxy_pass` 포트가 `.env.prod` 의 `API_PORT` 와 같은지, `/api/` 끝 슬래시가 있는지 |
| 롤백이 반복된다 | 서버에서 `./deploy.sh` 를 직접 돌려 진짜 실패 원인을 본다 |

---

## 5. 백업

`backup.sh` 가 pg_dump → 로컬 보관 → 30일 초과분 삭제를 한 번에 한다. **aws cli 는
선택 사항이다.** `BACKUP_S3_BUCKET` 을 채운 경우에만 원격 사본을 올린다.

```bash
cd /srv/certpilot/infra/deploy
./backup.sh --dry-run     # 무엇을 할지 확인
./backup.sh               # 실제 백업
```

| 변수 | 기본값 | 뜻 |
| --- | --- | --- |
| `BACKUP_DIR` | `/var/backups/certpilot` | 덤프를 남길 로컬 디렉터리. 미리 만들고 소유권을 준다 |
| `BACKUP_RETENTION_DAYS` | `30` | 로컬·원격 공통 보존 기간 |
| `BACKUP_S3_BUCKET` | (빈 값) | **비우면 로컬 보관만 한다.** 채우면 aws cli 로 업로드 |
| `BACKUP_S3_PREFIX` | `postgres` | 원격 키 접두사 |
| `BACKUP_S3_ENDPOINT` | (빈 값) | AWS 가 아닌 S3 호환 스토리지에 올릴 때만 |

`BACKUP_S3_BUCKET` 을 채웠는데 aws cli 가 없으면 스크립트는 **조용히 넘어가지 않고
실패한다**(설정 오류를 숨기지 않기 위해서다). 원격 사본이 필요 없으면 값을 비운다.

cron 등록은 `crontab.example` 을 그대로 쓴다(매일 04:10).

```bash
crontab /srv/certpilot/infra/deploy/crontab.example
crontab -l
```

`local-storage` 프로파일을 쓰는 중이라면 **원문 문서는 이 백업에 없다.** `minio_data`
볼륨이 유일한 사본이므로 따로 받는다(`crontab.example` 의 주간 tar 항목).

```bash
docker run --rm -v certpilot_minio_data:/data -v "$PWD:/out" alpine \
  tar czf "/out/minio-$(date -u +%Y%m%dT%H%M%SZ).tgz" -C /data .
```

복원:

```bash
docker run --rm --network host -v /var/backups/certpilot:/backup postgres:16-alpine \
  pg_restore --clean --if-exists --no-owner \
    -d "postgresql://certpilot:<암호>@<호스트>:5432/certpilot" \
    /backup/certpilot-<타임스탬프>.dump
```

`local-db` 프로파일이면 `<호스트>` 는 `localhost`(compose 가 5432 를 호스트에 게시하지
않으므로 `--network container:certpilot-postgres` 를 쓰거나 컨테이너 안에서 실행한다).
RDS 자동 백업(7일)과 이 논리 백업(30일)은 목적이 다르다. 전자는 인스턴스 장애 복구,
후자는 실수로 지운 데이터를 골라 되살리기 위한 것이다.

---

## 6. 운영 중 확인

```bash
cd /srv/certpilot/infra/deploy
COMPOSE="docker compose -f docker-compose.prod.yml --env-file .env.prod"
# 프로파일 컨테이너까지 보려면 --profile 을 함께 준다
# COMPOSE="$COMPOSE --profile local-db --profile local-storage"

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

디스크가 차면(오라클 무료 VM 부트 볼륨은 기본 47GB) 이미지 캐시부터 정리한다.

```bash
docker system df
docker system prune -af --filter "until=168h"
```

---

## 7. 데모 전 점검표

공통:

- [ ] `/health` 가 `{"status":"ok"}` — **VM 밖에서** 호출해 확인
- [ ] 데모 계정 4개로 로그인된다 (`make demo` 출력의 계정 표)
- [ ] 문서 12개가 "파싱 완료", 모의심사 판정 101개가 보인다
- [ ] 심사원 계정으로 초안 승인 전에는 다운로드가 막힌다 (D5)
- [ ] 대시보드에 변경 감지 알림과 사후심사 D-day 가 보인다
- [ ] 문서 업로드 → 다운로드가 왕복한다(오브젝트 스토리지 연결 확인)
- [ ] `./backup.sh --dry-run` 이 통과한다
- [ ] `.env.prod` 권한이 600 이고 git 에 올라가지 않았다 (`git status`)

오라클 VM 경로:

- [ ] VCN 보안 목록 **과** OS 방화벽 둘 다에서 필요한 포트만 열려 있다
- [ ] 22번 포트가 내 IP 로만 열려 있다
- [ ] `WEB_ORIGINS` 가 브라우저 주소창의 스킴·호스트·포트와 정확히 같다
- [ ] `WEB_ORIGIN_REGEX` 가 비어 있다(사설망 허용 정규식을 끈 상태)
- [ ] http 로 연다면 `SESSION_COOKIE_SECURE=false` 다
- [ ] `local-storage` 를 쓴다면 `minio_data` 볼륨 백업 계획이 있다
- [ ] `docker info --format '{{.Architecture}}'` 와 이미지 아키텍처가 맞는다

AWS 경로:

- [ ] EC2 보안그룹의 22번 포트가 내 IP 로만 열려 있다
- [ ] RDS 퍼블릭 액세스가 꺼져 있고 pgvector 확장이 켜져 있다
- [ ] `S3_REGION` 이 버킷 리전과 같다

---

## 8. 비용 감각 (데모 한 달 기준 대략)

**A. 오라클 클라우드 (Always Free 한도 안)**

| 항목 | 대략 |
| --- | --- |
| VM.Standard.A1.Flex 4 OCPU / 24GB | 0원 (Always Free 한도 내) |
| 부트 볼륨 50GB | 0원 (총 200GB 까지 무료) |
| Object Storage (쓴다면) | 10GB 까지 무료 |
| 아웃바운드 전송 | 월 10TB 까지 무료 |
| LLM API | 모의심사 1회 상한 5 USD (`app/workers/assess.py`) |

Always Free 한도와 정책은 바뀔 수 있다. 콘솔의 사용량을 직접 확인한다.

**B. AWS (ap-northeast-2)**

| 항목 | 대략 |
| --- | --- |
| EC2 t3.small | 월 20 USD 내외 |
| RDS db.t4g.micro + 20GB | 월 20 USD 내외 |
| S3 + 데이터 전송 | 월 1 USD 미만 |
| LLM API | 모의심사 1회 상한 5 USD |

발표가 끝나면 인스턴스를 지우거나 정지한다. 스냅샷만 남겨도 데모는 다시 살릴 수 있다.
