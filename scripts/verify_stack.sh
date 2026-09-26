#!/bin/sh
# 검증 전용 측면 스택. **원본 스택(api 8000 / web 8080 / mcp_db)은 건드리지 않는다.**
#
#   mcp-api-verify : 8001. 복사본 DB(mcp_db_verify)만 바라본다. entrypoint를 우회하므로
#                    alembic upgrade·seed가 돌지 않는다. 작업 트리의 backend/app을 **읽기 전용**으로
#                    마운트해 "지금 검증한 그 코드"가 그대로 실행된다(원본 이미지는 덮어쓰지 않는다 —
#                    이미지는 읽기만 하고, 코드는 마운트가 가린다).
#   mcp-web-verify : 8081. 작업 트리 frontend를 서빙하고 /api/ 를 8001로 프록시한다.
#
# 비밀값은 이 파일에 없다 — DB 비밀번호는 실행 시 .env에서 읽어 넘길 뿐 저장하지 않는다.
#
# 사용:
#   sh scripts/verify_stack.sh up      # 수집 전면 차단 상태(기본). CSP 호출 0건.
#   sh scripts/verify_stack.sh revert  # 허용을 되돌리고 차단 상태를 점검까지 한다
#   sh scripts/verify_stack.sh down
#
# 제한 실수집을 승인받았을 때만(계정 1개·수동만):
#   VERIFY_COST_INGEST_PROVIDERS=azure \
#   VERIFY_COST_INGEST_ACCOUNT_IDS=azure:<cloud_account_id> \
#   sh scripts/verify_stack.sh up
#
# 기본 동작(환경변수를 주지 않음) = 수동·자동 수집 **모두 꺼짐**. 빈 provider 목록은 "전체 허용"이
# 아니라 "한 곳도 허용 안 함"이다(app/cost/gating.py). 자동 수집은 이 스택에서 **항상** 꺼 둔다.
#
# 순서(제한 실수집):
#   1) 허용 env로 up  →  2) `verify_preflight.sh allow <계정id>`  (**실패하면 여기서 멈춘다 — 수집 금지**)
#   3) 수동 1회 실행(성공·실패 무관, 추가 실행 없음)
#   4) `verify_stack.sh revert` 로 허용 원복  →  5) `verify_preflight.sh blocked` 로 차단 확인
#   6) 그 다음에 결과를 보고한다(원복·재확인 전에 보고하지 않는다)
#
# DB 가드: 복사본 이름(mcp_db_verify…) 외에는 기동 전에 거부한다 — 원본(mcp_db)에 붙을 수 없다.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="${VERIFY_IMAGE:-multi-cloud-platform-api:latest}"
NET="${VERIFY_NET:-multi-cloud-platform_default}"
# docker context를 **명시적으로** 고른다 — 기본 프로필(원본 스택이 있는 데몬)에 실수로 붙지 않게.
# 별도 검증 프로필을 쓰면 colima가 만드는 context 이름이 colima-<프로필>이다.
CTX="${VERIFY_DOCKER_CONTEXT:-colima-verify}"
DOCKER="docker --context $CTX"
DB_HOST="${VERIFY_DB_HOST:-db}"
CONF="$ROOT/scripts/verify-nginx.conf"
VERIFY_DB="${VERIFY_DB_NAME:-mcp_db_verify}"
# 원본 DB에 붙는 사고를 기동 **전에** 막는다. 허용 이름은 mcp_db_verify 로 시작하는 것뿐이다.
case "$VERIFY_DB" in
  mcp_db_verify*) ;;
  *) echo "거부: 검증용 DB 이름이 아닙니다 → '$VERIFY_DB' (허용: mcp_db_verify…)" >&2; exit 2 ;;
esac

# 제한 실수집 상한 — 컨테이너에 **명시적으로** 넘긴다(docker-compose.yml은 이 값을 전달하지 않는다).
MAX_PAGES="${VERIFY_COST_AZURE_MAX_PAGES:-2}"
MAX_REQUESTS="${VERIFY_COST_AZURE_MAX_REQUESTS:-3}"
MAX_WAIT="${VERIFY_COST_AZURE_MAX_RETRY_WAIT_SECONDS:-30}"

case "${1:-up}" in
  revert)
    # 실수집 뒤 원복: 허용 env 없이 다시 띄우면 수동·자동 모두 빈 목록으로 돌아간다.
    VERIFY_COST_INGEST_PROVIDERS= VERIFY_COST_INGEST_ACCOUNT_IDS= sh "$0" up
    echo "── 원복 확인(차단 상태) ──"
    sh "$(dirname "$0")/verify_preflight.sh" blocked
    ;;
  down)
    $DOCKER stop mcp-web-verify mcp-api-verify >/dev/null 2>&1 || true
    echo "측면 컨테이너 중지. 복사본 DB($VERIFY_DB)와 원본 스택은 그대로."
    ;;
  up)
    [ -f "$ROOT/.env" ] || { echo ".env가 없습니다: $ROOT/.env" >&2; exit 1; }
    DBPW="$(grep -oE 'mcp_user:[^@]+@db' "$ROOT/.env" | head -1 | sed 's/mcp_user://;s/@db//')"
    [ -n "$DBPW" ] || { echo ".env에서 DB 비밀번호를 찾지 못했습니다" >&2; exit 1; }
    $DOCKER ps -q -f name=mcp-api-verify | grep -q . && $DOCKER stop mcp-api-verify >/dev/null || true
    $DOCKER ps -q -f name=mcp-web-verify | grep -q . && $DOCKER stop mcp-web-verify >/dev/null || true
    # 격리: .env를 상속하되 **밖으로 나가는 경로를 전부 덮어쓴다**(앱 기본값이 안전하다는 이유로
    # 믿지 않는다 — .env가 바뀌면 그대로 뚫린다).
    $DOCKER run -d --rm --name mcp-api-verify --network "$NET" --env-file "$ROOT/.env" \
      -v "$ROOT/backend/app:/app/app:ro" \
      -e DATABASE_URL="postgresql+psycopg2://mcp_user:${DBPW}@${DB_HOST}:5432/${VERIFY_DB}" \
      -e COST_SCHEDULER_ENABLED=false -e REPORT_SCHEDULER_ENABLED=false \
      -e MAIL_HOST=mailhog -e MAIL_PORT=1025 -e MAIL_USERNAME= -e MAIL_PASSWORD= -e MAIL_USE_TLS=false \
      -e COST_INGEST_PROVIDERS="${VERIFY_COST_INGEST_PROVIDERS:-}" \
      -e COST_AUTO_INGEST_PROVIDERS= \
      -e COST_INGEST_ACCOUNT_IDS="${VERIFY_COST_INGEST_ACCOUNT_IDS:-}" \
      -e COST_AZURE_MAX_PAGES="$MAX_PAGES" \
      -e COST_AZURE_MAX_REQUESTS="$MAX_REQUESTS" \
      -e COST_AZURE_MAX_RETRY_WAIT_SECONDS="$MAX_WAIT" \
      -p 8001:8000 --entrypoint "" "$IMAGE" uvicorn app.main:app --host 0.0.0.0 --port 8000 >/dev/null
    $DOCKER run -d --rm --name mcp-web-verify --network "$NET" -p 8081:80 \
      -v "$ROOT/frontend:/usr/share/nginx/html:ro" \
      -v "$CONF:/etc/nginx/conf.d/default.conf:ro" nginx:1.27-alpine >/dev/null
    printf "api 8001 → "; curl -s -m 5 localhost:8001/health || echo "(응답 없음)"; echo
    printf "web 8081 → "; curl -s -m 5 -o /dev/null -w "%{http_code}\n" localhost:8081/cost.html
    echo "context: $CTX · 네트워크: $NET · DB: $VERIFY_DB@$DB_HOST · 이미지: $IMAGE(변경하지 않음)"
    echo "코드: $ROOT/backend/app → /app/app (읽기 전용)"
    echo "수집 허용(수동): '${VERIFY_COST_INGEST_PROVIDERS:-(비어 있음 = 전면 차단)}'" \
         "· 계정: '${VERIFY_COST_INGEST_ACCOUNT_IDS:-(비어 있음)}' · 자동: 항상 차단"
    echo "Azure 상한: 페이지 $MAX_PAGES · 요청 $MAX_REQUESTS · 429 최대 대기 ${MAX_WAIT}s"
    echo "확인: sh scripts/verify_preflight.sh blocked   (실수집 직전에는 allow <계정id>)"
    echo "점검이 실패하면 수집을 실행하지 않는다."
    ;;
  *) echo "usage: $0 [up|down|revert]"; exit 1 ;;
esac
