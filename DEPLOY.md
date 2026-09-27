# دیپلوی ONEX 1.3.10

## Railway
1. محتوای این پوشه رو push کن روی ریپو (جایگزین نسخه قبلی).
2. Railway خودش `railway.json` رو می‌خونه و با Dockerfile بیلد می‌کنه.
3. یه **Volume** با mount path `/data` وصل کن (Railway خودش `RAILWAY_VOLUME_MOUNT_PATH` رو ست می‌کنه).
4. Networking: دامنه عمومی بساز، target port = `8080`.
5. بعد از دیپلوی تو لاگ باید ببینی: `SideRail core (xray) running`.
6. وضعیت هسته: `GET /api/siderail/status` (باید `engine: xray` و `running: true` باشه).

## Docker معمولی
```
docker build -t onex .
docker run -d -p 8080:8080 -v onex-data:/data --name onex onex
```

## متغیرهای اختیاری
- `XRAY_VERSION` / `SINGBOX_VERSION` (build arg): نسخه هسته‌ها
- `ONEX_SR_VMESS_PORT` (پیش‌فرض 18501)، `ONEX_SR_XHTTP_PORT` (پیش‌فرض 18503)
- `ONEX_XRAY_LOG_LEVEL` (پیش‌فرض warning)

نکته: لینک‌های VMess WS و SideRail VLESS XHTTP که قبلاً ساختی نیاز به ساخت دوباره ندارن؛ همون مسیرهای `/siderail/vmess` و `/siderail/xhttp` هستن.
