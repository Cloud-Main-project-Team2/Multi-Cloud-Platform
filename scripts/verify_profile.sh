#!/bin/sh
# 검증 전용 격리 환경(별도 colima 프로필)을 만들고 띄운다. **기본 프로필은 건드리지 않는다** —
# 원본 스택 컨테이너는 이 데몬에 존재하지도 않으므로 자동으로 뜰 수 없다.
#
#   colima 프로필 : verify            (docker context: colima-verify)
#   네트워크      : mcp-verify-net
#   컨테이너      : mcp-db-verify(postgres:16, named volume) · mcp-mailhog-verify(alias: mailhog)
#   이미지        : mcp-verify-api:local  ← 지금 코드로 **별도 태그** 빌드(원본 태그를 덮지 않는다)
#   DB            : 빈 mcp_db_verify 에 alembic head + 자격증명 없는 최소 시드
#
# 비밀값은 이 파일에 없다 — DB 비밀번호는 실행 시 .env에서 읽는다.
# 사용: sh scripts/verify_profile.sh [up|stop|status]
#   up     프로필·네트워크·DB·MailHog·이미지·마이그레이션·시드까지(모두 멱등)
#   stop   검증 컨테이너와 프로필만 정지(DB 볼륨은 보존 — 다음에 이어서 쓴다)
#   status 지금 상태만 출력
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROFILE="${VERIFY_PROFILE:-verify}"
CTX="colima-$PROFILE"
DOCKER="docker --context $CTX"
NET=mcp-verify-net
DB_C=mcp-db-verify
MH_C=mcp-mailhog-verify
VOL=mcp-verify-pgdata
IMAGE=mcp-verify-api:local
DB_NAME=mcp_db_verify

dbpw() {
  grep -oE 'mcp_user:[^@]+@db' "$ROOT/.env" | head -1 | sed 's/mcp_user://;s/@db//'
}

case "${1:-up}" in
  status)
    colima list | sed -n "1p;/$PROFILE/p"
    $DOCKER ps -a --format '{{.Names}}\t{{.Status}}' 2>/dev/null || echo "(프로필이 꺼져 있습니다)"
    ;;
  stop)
    $DOCKER stop mcp-web-verify mcp-api-verify >/dev/null 2>&1 || true
    $DOCKER stop "$MH_C" "$DB_C" >/dev/null 2>&1 || true
    colima stop --profile "$PROFILE"
    echo "정지 완료. 검증 DB 볼륨($VOL)은 보존 — 다음에 'up'으로 이어서 쓴다."
    ;;
  up)
    [ -f "$ROOT/.env" ] || { echo ".env가 없습니다" >&2; exit 1; }
    colima list | grep -qE "^$PROFILE +Running" || colima start --profile "$PROFILE" --cpu 2 --memory 4 --disk 30
    $DOCKER network inspect "$NET" >/dev/null 2>&1 || $DOCKER network create "$NET" >/dev/null
    $DOCKER volume inspect "$VOL" >/dev/null 2>&1 || $DOCKER volume create "$VOL" >/dev/null

    if ! $DOCKER ps -a --format '{{.Names}}' | grep -qx "$DB_C"; then
      $DOCKER run -d --name "$DB_C" --network "$NET" \
        -e POSTGRES_USER=mcp_user -e POSTGRES_PASSWORD="$(dbpw)" -e POSTGRES_DB="$DB_NAME" \
        -v "$VOL:/var/lib/postgresql/data" postgres:16 >/dev/null
    else
      $DOCKER start "$DB_C" >/dev/null 2>&1 || true
    fi
    if ! $DOCKER ps -a --format '{{.Names}}' | grep -qx "$MH_C"; then
      $DOCKER run -d --name "$MH_C" --network "$NET" --network-alias mailhog mailhog/mailhog:v1.0.1 >/dev/null
    else
      $DOCKER start "$MH_C" >/dev/null 2>&1 || true
    fi

    # 지금 코드로 검증 전용 태그를 빌드한다(원본 태그와 이름이 다르고, 프로필이 달라 저장소도 별개다).
    $DOCKER build -q -t "$IMAGE" "$ROOT/backend" >/dev/null
    URL="postgresql+psycopg2://mcp_user:$(dbpw)@$DB_C:5432/$DB_NAME"
    # DB가 받을 준비가 될 때까지(최대 30초)
    i=0; until $DOCKER exec "$DB_C" pg_isready -U mcp_user -d "$DB_NAME" >/dev/null 2>&1 || [ $i -ge 30 ]; do i=$((i+1)); sleep 1; done
    $DOCKER run --rm --network "$NET" -e DATABASE_URL="$URL" --entrypoint "" "$IMAGE" alembic upgrade head 2>&1 | tail -1
    $DOCKER run --rm --network "$NET" -v "$ROOT/scripts:/seed:ro" -w /app -e PYTHONPATH=/app \
      --env-file "$ROOT/.env" -e DATABASE_URL="$URL" --entrypoint "" "$IMAGE" python /seed/verify_seed_min_data.py | tail -3

    echo
    echo "다음: VERIFY_DOCKER_CONTEXT=$CTX VERIFY_NET=$NET VERIFY_DB_HOST=$DB_C VERIFY_IMAGE=$IMAGE sh scripts/verify_stack.sh up"
    echo "그다음: VERIFY_DOCKER_CONTEXT=$CTX sh scripts/verify_preflight.sh blocked"
    ;;
  *) echo "usage: $0 [up|stop|status]"; exit 1 ;;
esac
