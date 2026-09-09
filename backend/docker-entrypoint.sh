#!/bin/sh
set -e

if [ "$1" = "uvicorn" ]; then
  alembic upgrade head
  python -m app.seed
fi

exec "$@"
