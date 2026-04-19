# sais_web — Proje Notları

Bu dosya Claude Code için proje rehberidir. Geliştirmeye başlamadan önce okunması önerilir.

## Proje amacı

`sais_web`, **atıksu sürekli izleme** istasyonlarından (SAIS) gelen ölçüm verilerini toplayan, saklayan ve REST API ile sunan bir **web SCADA uygulamasıdır**. Django 5.2 + Django REST Framework üzerine kuruludur.

Mimari iki katmana ayrıldı:

- **`api/`** — jenerik SCADA çekirdeği. Sektör-özel hiçbir kavram içermez. Modeller: `Station`, `StationType`, `Connection`, `Parameter`, `Sensor`, `SensorLatest`, `Reading`, `Calibration`, `PowerOff`, `Command`, `RequestType`, `StatusCode`, `LogType`, `SystemLog`, `ApiLog`.
- **`sais_domain/`** — SAIS'e (Çevre ve Şehircilik Bakanlığı atıksu izleme rejimi) özgü uzantılar. Modeller: `SaisCabinet` (Bakanlık SIM ID + erişim bilgileri), `EnvisoftChannel` (Parameter ↔ Envisoft kanal eşlemesi). Atıksu istasyon tipleri ve "Bakanlık Numune Talebi" gibi alan-özel lookup kayıtları `seed_sais_data` ile yüklenir.

Çekirdek özellikler:

- Modbus TCP, Modbus RTU/ASCII (serial) ve özel ASCII (request-response) protokol desteği — `Connection.protocol` alanı ile (kapsam kasten Modbus + ASCII ile sınırlı; OPC UA/MQTT/HTTP REST yok)
- Çok istasyonlu yapı (`Station` → `Connection` → `Sensor` → `Reading`)
- Parametre/sensör/status şemaları veri modelinde ayrık; sensörde tipli veri (`data_type`, `scale`, `offset`, `bit_position`) ve protokol-özel alanlar (Modbus için slave/function/address, ASCII için request/response/terminator)
- Komut (write/aksiyon) modeli `Command` — state machine (pending→queued→executing→completed/failed/timeout/expired/cancelled), idempotency, lifecycle timestamps, audit
- Kalibrasyon ve power-off (açılma/kapanma) kayıtları
- Otomatik **API request logging** — gelen ve giden tüm HTTP çağrıları `ApiLog(direction='in'/'out')` olarak kayıt; hassas header/body maskeli; 90 günlük retention

## Teknoloji yığını

| Katman | Teknoloji |
|---|---|
| Runtime | Python 3.12 |
| Web framework | Django 5.2 LTS |
| API | Django REST Framework + dj-rest-auth |
| Admin tema | django-jazzmin |
| Veritabanı | Microsoft SQL Server 2022 (mssql-django + pyodbc, ODBC Driver 17/18) |
| Cache | Redis 7 (django-redis) |
| Statik | whitenoise (brotli) |
| Endüstriyel IO | pymodbus 3.x, pyserial |
| Veri işleme | pandas, numpy |
| Prod sunucu | gunicorn |
| Container | Docker + docker-compose |

## Proje yapısı

```
sais_web/
├── sais_web/              # Django project root (settings, urls, wsgi, asgi)
├── api/                   # Jenerik SCADA çekirdeği
│   ├── models.py          # Station, Connection, Parameter, Sensor, Reading, Command, ApiLog vs.
│   ├── views.py           # REST API endpoint'leri (bazıları SAIS-flavor; bkz. mimari notu)
│   ├── serializers.py
│   ├── middleware.py      # ApiLoggingMiddleware — gelen istek auto-log
│   ├── api_logging.py     # Outbound helper (log_outbound_call decorator + record_outbound_call)
│   ├── signals.py         # SIGTERM/SIGINT yakalar, PowerOff kaydı kapatır
│   ├── helpers.py         # DataFrame → JSON safe dönüşüm
│   ├── permissions.py
│   ├── management/commands/
│   │   ├── seed_initial_data.py    # Çekirdek seed: Station, Parameter, StatusCode, RequestType
│   │   └── prune_api_logs.py       # 90 günlük ApiLog retention
│   └── migrations/
├── sais_domain/           # SAIS-özel uzantılar (Bakanlık + Envisoft entegrasyonu)
│   ├── models.py          # SaisCabinet, EnvisoftChannel
│   ├── admin.py
│   ├── apps.py
│   ├── management/commands/seed_sais_data.py  # Atıksu StationType + ministry_sample + Envisoft eşlemeleri
│   └── migrations/
├── users/                 # CustomUser (rol tabanlı)
├── modbus/                # pymodbus reader (TCP + serial, Connection.protocol bazlı)
├── templates/             # error-404.html
├── static/                # images/logo + css
├── manage.py
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example           # Konfigürasyon şablonu
└── .gitignore / .dockerignore / .claudeignore
```

## Geliştirme ortamı

### İlk kurulum

```powershell
# Sanal ortam
C:\Users\<user>\AppData\Local\Programs\Python\Python312\python.exe -m venv venv
venv\Scripts\activate

# Bağımlılıklar
pip install -r requirements.txt

# Env dosyası
copy .env.example .env
# .env dosyasındaki POSTGRES_PASSWORD vb. alanları düzenle

# DB kurulumu ve seed
python manage.py migrate
python manage.py seed_initial_data    # çekirdek: Station, Parameter, StatusCode, jenerik RequestType
python manage.py seed_sais_data       # SAIS: atıksu StationType, ministry_sample, EnvisoftChannel
python manage.py createsuperuser
python manage.py runserver
```

### Docker ile

```bash
docker compose up -d --build
docker compose exec web python manage.py migrate
docker compose exec web python manage.py seed_initial_data
docker compose exec web python manage.py seed_sais_data
docker compose exec web python manage.py createsuperuser
```

Servisler: `web` (Django), `db` (MSSQL Server 2022), `redis` (Redis 7).

> **Yerel geliştirme ön koşulu:** Host Windows'da **Microsoft ODBC Driver 17 veya 18 for SQL Server** kurulu olmalı. Kontrol: `python -c "import pyodbc; print(pyodbc.drivers())"`. Kurulu değilse [Microsoft indirme sayfasından](https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server) yüklenir.

## Önemli çalıştırma davranışları

- **Yapılandırma:** Tüm ayarlar `.env` üzerinden okunur (`python-dotenv`). Sırları asla koda commitlemeyin. Ek env'ler: `API_LOG_ENABLED`, `API_LOG_RETENTION_DAYS` (default 90), `API_LOG_MAX_BODY_CHARS` (default 10000), `API_LOG_SKIP_PATHS`.
- **DEBUG=False** modunda HSTS, güvenli çerez ve Whitenoise manifest storage aktiftir.
- **Seed komutları idempotenttir** — `seed_initial_data` ve `seed_sais_data` birden çok kez çalıştırmak güvenlidir (`get_or_create`).
- **Modbus okuma:** `modbus.reader.ModbusReader` `Connection.protocol` bazlı dallanır (`modbus_tcp` paralel, `modbus_rtu`/`modbus_ascii` serial). Başarılı/başarısız bağlantı `Connection.last_connected_at` / `last_error_at` / `last_error_message`'a yazar. **Periyodik çalıştırma için scheduler yok** (Celery beat / APScheduler eklenebilir).
- **Sensör decode** mevcut reader'da basit: register'dan ilk değeri okuyup `scale*raw + offset` uygular. `data_type` (int16/uint16/float32/float64/bool/bit), `bit_position`, `byte_order`/`word_order` doğru decode'u **henüz uygulanmadı** — TODO. Özel ASCII protokol reader'ı **yok** — TODO.
- **Komut tetikleme:** `StartSampleView` → `Command` tablosuna `idempotency_key='ministry_sample:{station}:{code}'` ile kayıt; `priority=10`, `expires_at=+5dk`. Pending Command'ları alıp Modbus/ASCII write yapan **executor worker yok** — TODO. State makinesi: pending → queued → executing → completed/failed/timeout/expired/cancelled.
- **API request logging:** `api.middleware.ApiLoggingMiddleware` her gelen isteği `ApiLog(direction='in')` olarak kaydeder; süre ölçer, `request.user`/IP/user-agent yakalar, hassas header (`Authorization`/`Cookie`/`X-API-Key`) ve body key'leri (`password`/`secret`/`token`/`api_key`) maskeli. Skip path'ler: `/static/`, `/media/`, `/__debug__/`, `/admin/jsi18n/`, `/favicon.ico`. Giden HTTP çağrıları için `api.api_logging.log_outbound_call` decorator veya `record_outbound_call(...)` helper kullanılır.
- **API log retention:** `python manage.py prune_api_logs` günlük cron ile çağrılmalı (`--days=N`, `--direction=in|out|all`, `--dry-run`).

## Kod düzenleme kuralları

- Mevcut dosyaların biçim stiline sadık kal (Türkçe verbose_name/help_text, snake_case tablo adları).
- **App sınırını koru:** jenerik SCADA kavramları `api/`'a, SAIS/Bakanlık/Envisoft özelinde olanlar `sais_domain/`'e gider. Yeni bir model/seed/komut eklerken hangi tarafa ait olduğunu sor.
- Veritabanı şema değişikliklerinde `python manage.py makemigrations` + `migrate` üret. MSSQL'de bazı manuel migration'lar (`SeparateDatabaseAndState`) gerekebilir; bkz. `api/migrations/0006_apilog_rebuild.py`.
- Yeni secret/ayar eklerken hem `.env.example` hem `settings.py`'yi güncelle.
- `api/views.py` — **Response sözleşmesi** tüm endpoint'lerde aynı: `{"result": bool, "message": str | null, "objects": any}`. Yeni endpoint eklerken bu formatı koru.
- REST endpoint default `IsAuthenticated`; public uç nokta eklenirken `permission_classes` açıkça override edilmeli.
- Dış HTTP çağrılarını `api.api_logging.log_outbound_call(component="...")` decorator'u veya `record_outbound_call(...)` helper'ı ile sar — `ApiLog`'da `direction='out'` kaydı otomatik yazılsın.
- `pyproject.toml`/`ruff`/`black` gibi linter yok — mevcut stili el ile koru.

## Testler

`api/tests.py`, `users/tests.py`, `modbus/tests.py` şu an boş. Yeni özellik eklerken ilgili uygulamaya test yazılması beklenir.

```bash
python manage.py test
```

## Git & commit kuralları

- Master branch: `master`. PR hedefi: `main` (GitHub'da).
- Bu projede **her mantıksal değişiklikten sonra otomatik commit + push** yapılır (kullanıcı isteği). `.env`, DB şifreleri, `venv/`, `media/` gibi dosyalar commitlenmez.
- Commit mesajları Conventional Commits: `feat:`, `fix:`, `chore:`, `docs:`, `refactor:` prefiksleri tercih edilir.

## Bilinen sınırlamalar / yol haritası

- **Scheduler yok** — Modbus reader'ı tetikleyen ve eski `Command`'ları "expired" yapan zamanlayıcı (Celery beat / APScheduler) yok.
- **Command executor yok** — `Command(status='pending')` satırlarını alıp `Sensor.connection.protocol`'e göre Modbus/ASCII write yapan worker eklenmedi. State machine ve idempotency_key altyapısı hazır, sadece executor eksik.
- **Reader decode eksik** — `modbus/reader.py` register'ın ilk değerini alıyor; `Sensor.data_type` (int16/uint16/float32/...), `bit_position`, `byte_order`/`word_order` doğru decode'u yok. ASCII protokol reader'ı yok (`Connection.protocol='ascii_custom'` desteklenmiyor).
- **`api/urls.py` boş** — tüm endpoint'ler `sais_web/urls.py` içinde tanımlı. Genişlerken `api/urls.py`'ye taşımak temiz olur.
- **API view-app sınır ihlali** — `api/views.py` `sais_domain.SaisCabinet`'i import ediyor (stationId = Bakanlık SIM ID üzerinden filtrelediği için). Bu endpoint'ler (`GetData`, `GetStationInformation`, `StartSample` vb.) aslında SAIS-flavor; ileride `sais_domain`'e (veya yeni bir `sais_api` app'ine) taşımak `api/`'yı tamamen jenerik bırakır.
- **`users` uygulamasının `views.py`'si minimal** — rol bazlı ön yüz akışı ileride eklenecek.
- **`pymodbus 3.x` API drift** — reader hala `method=`/`unit=` parametrelerini kullanıyor; pymodbus 3.x'te `slave=` ve serial mode'da `framer=` kullanılmalı (TODO).
- **Plaintext credentials** — `SaisCabinet.auth_secret` ve `Connection.auth_secret` yok artık (Connection'dan kaldırıldı, scope dışı tutuldu); SaisCabinet'teki şifre `django-fernet-fields` veya bir KMS ile şifrelenmeli (TODO).
- **API log retention cron** — `prune_api_logs` günlük tetiklenmeli. Linux örnek: `0 3 * * * cd /app && python manage.py prune_api_logs`. Windows'ta Task Scheduler ile aynı komut.
