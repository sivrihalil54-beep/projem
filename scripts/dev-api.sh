#!/usr/bin/env bash
# Yalnızca FastAPI (127.0.0.1:8000).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
elif [[ -x "$ROOT/venv/bin/python" ]]; then
  PY="$ROOT/venv/bin/python"
else
  echo "TEYIT | DEV_API | HATA | .venv veya venv bulunamadı." >&2
  exit 1
fi

exec "$PY" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
