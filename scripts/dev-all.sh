#!/usr/bin/env bash
# Proje kökünden: API (8000) + Vite panel (5173). venv ile .venv yolunu otomatik seçer.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY=""
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
elif [[ -x "$ROOT/venv/bin/python" ]]; then
  PY="$ROOT/venv/bin/python"
else
  echo "TEYIT | DEV_ALL | HATA | Sanal ortam bulunamadı (.venv veya venv)." >&2
  echo "  Oluşturun: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

API_CMD="$PY -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload"

exec npx concurrently \
  -k \
  -n api,web \
  -c blue,magenta \
  "$API_CMD" \
  "npm run dev --prefix web"
