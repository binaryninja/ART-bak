#!/usr/bin/env bash
# Development: run backend (:8787), traffic proxy (:8788) and Next dev frontend (:5173) together.
# Frontend /api requests proxy to the backend; Ctrl-C stops both.
#
# For the embedded-frontend single binary, see the README single-binary section instead.
set -euo pipefail
cd "$(dirname "$0")"

# On exit, terminate all children in this process group (backend and frontend).
cleanup() { kill 0 2>/dev/null || true; }
trap cleanup EXIT INT TERM

# Backend (plain go run, no embedded frontend); configure concurrent work agents in System Settings.
go run ./cmd/artex -addr :8787 -proxy 127.0.0.1:8788 &

# Frontend hot reload (Vite/Next dev server; /api proxies to :8787).
( cd web && npm run dev ) &

echo "[dev] Backend :8787 / Proxy :8788 / Frontend http://localhost:5173  (Ctrl-C to exit)"
wait
