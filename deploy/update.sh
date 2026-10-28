#!/usr/bin/env bash
# تحديث النظام إلى نسخة جديدة: ضع الملفات الجديدة فوق القديمة ثم شغّل هذا الملف. البيانات محفوظة في volume ولا تتأثر.
set -euo pipefail
cd "$(dirname "$0")"
docker compose up -d --build
echo "تم التحديث."
