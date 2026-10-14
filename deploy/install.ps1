# تثبيت/تحديث النظام على سيرفر Windows (يتطلب Docker Desktop مثبتًا ومشغّلًا)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  Write-Host "!! Docker غير مثبت. حمّل Docker Desktop من https://www.docker.com/products/docker-desktop ثم أعد التشغيل."; exit 1
}
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Write-Host ">> أُنشئ ملف .env من النموذج." }
Write-Host ">> بناء الصورة وتشغيل الخدمة..."
docker compose up -d --build
$port = (Select-String -Path ".env" -Pattern '^HOST_PORT=(\d+)' | ForEach-Object { $_.Matches[0].Groups[1].Value })
if (-not $port) { $port = 8080 }
for ($i = 0; $i -lt 30; $i++) {
  try { $r = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$port/healthz" -TimeoutSec 5; if ($r.StatusCode -eq 200) {
    Write-Host "`n==============================================="
    Write-Host " تم التشغيل بنجاح. افتح: http://localhost:$port  (ومن الأجهزة الأخرى: http://اسم-السيرفر:$port)"
    Write-Host " أول حساب يُسجَّل يصبح مشرفًا. لوحة التحكم: /admin"
    Write-Host "==============================================="; exit 0 } } catch { Start-Sleep -Seconds 2 }
}
Write-Host "!! الخدمة لم تستجب بعد. اعرض السجل: docker compose logs -f"; exit 1
