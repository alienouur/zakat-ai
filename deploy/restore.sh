#!/usr/bin/env bash
# استرجاع نسخة احتياطية: ./restore.sh backups/zakat-2026-01-01_0900.db
set -euo pipefail
cd "$(dirname "$0")"
[ -f "${1:-}" ] || { echo "استخدم: ./restore.sh <ملف النسخة .db>"; exit 1; }
docker compose cp "$1" zakat-ai:/data/zakat.db
docker compose restart zakat-ai
echo "تم الاسترجاع وإعادة التشغيل."
