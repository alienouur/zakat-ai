#!/usr/bin/env bash
# نسخة احتياطية من قاعدة البيانات (الحسابات والمحادثات) إلى مجلد backups/
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p backups
STAMP=$(date +%Y-%m-%d_%H%M)
docker compose exec -T zakat-ai python -c "import sqlite3; s=sqlite3.connect('/data/zakat.db'); d=sqlite3.connect('/data/backup.db'); s.backup(d); d.close()"
docker compose cp zakat-ai:/data/backup.db "backups/zakat-${STAMP}.db"
echo "تم: backups/zakat-${STAMP}.db"
# بيانات الشات بوت العام (المحادثات، سجل الطلبات، الرسائل غير المرسلة) إن كانت الخدمة تعمل
if docker compose ps --services --status running 2>/dev/null | grep -qx zakat-chatbot; then
  docker compose exec -T zakat-chatbot sh -c "cd /data && tar czf /tmp/chatbot-backup.tgz ." && \
  docker compose cp zakat-chatbot:/tmp/chatbot-backup.tgz "backups/chatbot-${STAMP}.tgz" && echo "تم: backups/chatbot-${STAMP}.tgz"
fi
ls -1t backups | tail -n +31 | sed 's#^#backups/#' | xargs -r rm -f   # نحتفظ بآخر 30 نسخة
