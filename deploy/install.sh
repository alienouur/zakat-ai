#!/usr/bin/env bash
# تثبيت/تحديث النظام على سيرفر Linux (Ubuntu/Debian/RHEL) بأمر واحد.
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v docker >/dev/null 2>&1; then
  echo ">> Docker غير مثبّت. جارٍ التثبيت (يحتاج صلاحيات sudo)..."
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker "$USER" || true
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "!! أمر 'docker compose' غير متوفر. ثبّت Docker Compose v2 ثم أعد تشغيل هذا الملف."; exit 1
fi
if [ ! -f .env ]; then
  cp .env.example .env
  echo ">> أُنشئ ملف .env من النموذج. يمكنك تعديله لاحقًا (المنفذ، رمز الدعوة...)."
fi

echo ">> بناء الصورة وتشغيل الخدمة..."
docker compose up -d --build

PORT=$(grep -E '^HOST_PORT=' .env | cut -d= -f2); PORT=${PORT:-8080}
echo ">> انتظار جاهزية الخدمة..."
for i in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; then
    echo
    echo "================================================================"
    echo " تم التشغيل بنجاح. افتح من أي جهاز داخل الشبكة:"
    echo "   http://$(hostname -I 2>/dev/null | awk '{print $1}'):${PORT}"
    echo " أول حساب يُسجَّل يصبح مشرفًا. لوحة التحكم: /admin"
    echo "================================================================"
    exit 0
  fi
  sleep 2
done
echo "!! الخدمة لم تستجب بعد. اعرض السجل بالأمر: docker compose logs -f"; exit 1
