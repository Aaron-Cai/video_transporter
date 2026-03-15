#!/usr/bin/env sh

set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if ! command -v uv >/dev/null 2>&1; then
  echo "Required command 'uv' was not found in PATH." >&2
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "Required command 'npm' was not found in PATH." >&2
  exit 1
fi

VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python"

cleanup() {
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
}

trap cleanup INT TERM EXIT

(
  cd "$PROJECT_ROOT"
  if [ -x "$VENV_PYTHON" ]; then
    echo "Using existing .venv for backend startup..."
    exec "$VENV_PYTHON" -m uvicorn backend.app.main:app --reload
  fi

  echo "No existing .venv found. Creating backend environment with uv..."
  uv sync
  exec uv run uvicorn backend.app.main:app --reload
) &
BACKEND_PID=$!

(
  cd "$PROJECT_ROOT/frontend"
  npm install
  exec npm run dev
) &
FRONTEND_PID=$!

echo "Backend and frontend are starting in this terminal..."
echo "Backend:  http://127.0.0.1:8000"
echo "Frontend: http://127.0.0.1:5173"

wait "$BACKEND_PID" "$FRONTEND_PID"
