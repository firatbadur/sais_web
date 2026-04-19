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
| Async kuyruk | Celery 5.x + Redis broker |
| Periyodik task | django-celery-beat (DatabaseScheduler) |
| Task sonuç backend | django-celery-results (django-db) |
| Veri işleme | pandas, numpy |
| Prod sunucu | gunicorn |
| Container | Docker + docker-compose (web + celery_worker + celery_beat + db + redis) |

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
├── scada_io/              # SCADA reader/writer çekirdeği (Modbus + ASCII)
│   ├── decoders.py        # decode_registers / encode_value (int16/uint32/float32/bool/bit/string)
│   ├── persistence.py     # persist_reading — Reading insert + SensorLatest upsert (atomik)
│   ├── readers/           # ProtocolReader: base / modbus_tcp / modbus_serial / ascii_custom + factory
│   ├── writers/           # ProtocolWriter: base / modbus_tcp / modbus_serial / ascii_custom + factory
│   ├── tasks.py           # Celery: dispatch_polls / poll_connection / dispatch_commands / execute_command / expire_commands
│   ├── tests.py           # Decoder unit tests (12 data_type × byte/word combos)
│   └── management/commands/
│       ├── seed_periodic_tasks.py   # django_celery_beat PeriodicTask kayıtları
│       ├── poll_once.py             # debug: tek bağlantıyı sync polla
│       └── execute_command_now.py   # debug: tek komutu sync yürüt
├── sais_web/celery.py     # Celery app instance — autodiscover_tasks
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
python manage.py seed_periodic_tasks  # 7 Celery beat periodic task (idempotent)
python manage.py createsuperuser
python manage.py runserver

# Lokal Celery (worker + beat tek process — sadece dev)
celery -A sais_web worker -B -l info
# Windows'ta `-P solo` ekle: celery -A sais_web worker -B -l info -P solo
```

### Docker ile

```bash
docker compose up -d --build
# web servisi otomatik migrate + seed_periodic_tasks yapar
docker compose exec web python manage.py seed_initial_data
docker compose exec web python manage.py seed_sais_data
docker compose exec web python manage.py createsuperuser
```

Servisler: `web` (Django), `celery_worker`, `celery_beat`, `db` (MSSQL Server 2022), `redis` (Redis 7).

> **Yerel geliştirme ön koşulu:** Host Windows'da **Microsoft ODBC Driver 17 veya 18 for SQL Server** kurulu olmalı. Kontrol: `python -c "import pyodbc; print(pyodbc.drivers())"`. Kurulu değilse [Microsoft indirme sayfasından](https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server) yüklenir.

## Önemli çalıştırma davranışları

- **Yapılandırma:** Tüm ayarlar `.env` üzerinden okunur (`python-dotenv`). Sırları asla koda commitlemeyin. Ek env'ler: `API_LOG_*`, `CELERY_BROKER_URL` (default `redis://localhost:6379/2`), `CELERY_RESULT_BACKEND` (default `django-db`).
- **DEBUG=False** modunda HSTS, güvenli çerez ve Whitenoise manifest storage aktiftir.
- **Seed komutları idempotenttir** — `seed_initial_data` ve `seed_sais_data` birden çok kez çalıştırmak güvenlidir (`get_or_create`).
- **SCADA polling:** `scada_io.tasks.dispatch_polls` Celery beat ile her 5 sn'de tetiklenir; `Connection.last_polled_at + poll_interval_sec` due olan bağlantılar için `poll_connection.delay(conn_id)` enqueue eder. Worker reader'ı açar, **per-connection** mantığında o bus'taki tüm aktif sensörleri sırayla okur, kapatır. Reader factory `Connection.protocol` bazlı: `modbus_tcp` (pymodbus TCP), `modbus_rtu`/`modbus_ascii` (pymodbus serial, framer parametresiyle), `ascii_custom` (pyserial request-response).
- **Sensör decode:** `scada_io.decoders.decode_registers` 12 data_type (int16/uint16/int32/uint32/int64/uint64/float32/float64/bool/bit/string/raw) × byte_order × word_order kombinasyonlarını destekler. `Sensor.scale * raw + Sensor.offset` mühendislik dönüşümü uygulanır. Bit-okuma: register içindeki `bit_position`. ASCII: `ascii_request` gönder + `ascii_response_regex` ile değer çıkar.
- **Sensör simülasyonu:** `Sensor.is_simulated=True` ise reader cihaza dokunmaz; `scada_io.tasks._record_simulated` `parameter.min_range`/`max_range` arası rastgele değer üretir (`Reading.origin='simulated'`).
- **Reading vs SensorLatest:** `SensorLatest` per-sensor snapshot (HMI hızlı okuma); `Reading` time-series historian. `scada_io.persistence.persist_reading` ikisini atomik olarak günceller (Reading insert + SensorLatest upsert; değer değiştiyse `last_change_at` güncellenir, `update_count` artırılır).
- **Aggregate tabloları:** `ReadingFifteenMin` / `ReadingHourly` / `ReadingDaily` — sensör başına bucket avg/min/max/count/bad_count. Dashboard'lar bunları sorgulasın, raw `Reading`'i taramasın. `aggregate_readings` komutu (manuel) veya `api.tasks.aggregate_readings_*` Celery task'larıyla (otomatik) doldurulur.
- **Komut tetikleme:** `StartSampleView` → `Command` tablosuna `idempotency_key='ministry_sample:{station}:{code}'` ile kayıt; `priority=10`, `expires_at=+5dk`. `scada_io.tasks.dispatch_commands` her 3 sn'de pending komutları atomik `pending → queued` yapıp `execute_command.delay(cmd_id)` enqueue eder. Worker writer açar, yazar, status `executing → completed/failed`. Başarısızsa `attempt_count < max_attempts` iken `pending`'e geri döner (otomatik retry); `expire_commands` her 60 sn'de süresi dolanları `expired` yapar.
- **API request logging:** `api.middleware.ApiLoggingMiddleware` her gelen isteği `ApiLog(direction='in')` olarak kaydeder; süre ölçer, `request.user`/IP/user-agent yakalar, hassas header (`Authorization`/`Cookie`/`X-API-Key`) ve body key'leri (`password`/`secret`/`token`/`api_key`) maskeli. Skip path'ler: `/static/`, `/media/`, `/__debug__/`, `/admin/jsi18n/`, `/favicon.ico`. Giden HTTP çağrıları için `api.api_logging.log_outbound_call` decorator veya `record_outbound_call(...)` helper kullanılır.
- **API log retention:** `python manage.py prune_api_logs` günlük cron ile çağrılmalı (`--days=N`, `--direction=in|out|all`, `--dry-run`).

## Periyodik task'lar (Celery beat)

Tüm periyodik iş Celery beat'in DB'de tuttuğu `PeriodicTask` kayıtlarıyla yönetilir — `seed_periodic_tasks` komutu idempotent olarak yazar:

| Task | Periyot | Amaç |
|---|---|---|
| `scada_io.tasks.dispatch_polls` | her 5 sn | Due olan Connection'lara `poll_connection` enqueue |
| `scada_io.tasks.dispatch_commands` | her 3 sn | Pending Command'lara `execute_command` enqueue |
| `scada_io.tasks.expire_commands` | her 60 sn | `expires_at` geçmiş komutları `expired` yap |
| `api.tasks.aggregate_readings_15m` | `*/5 * * * *` | Son 2 saatlik 15dk bucket aggregate |
| `api.tasks.aggregate_readings_hourly` | `5 * * * *` | Son 6 saatlik saatlik aggregate |
| `api.tasks.aggregate_readings_daily` | `0 1 * * *` | Son 48 saatlik günlük aggregate |
| `api.tasks.prune_api_logs_task` | `0 3 * * *` | `API_LOG_RETENTION_DAYS`'den eski kayıtları sil |

Yönetim:
- Admin panelinden (`/admin/django_celery_beat/periodictask/`) bireysel task'lar enable/disable edilebilir veya periyot değiştirilebilir.
- Yeni task eklenirse `seed_periodic_tasks` listesine ekle ve yeniden çalıştır (idempotent).

Manuel çalıştırma (debug):
```bash
python manage.py poll_once <connection_id>             # tek bağlantıyı sync polla
python manage.py execute_command_now <command_id>      # tek komutu sync yürüt
python manage.py aggregate_readings --bucket=15m       # aggregate'i manuel tetikle
python manage.py prune_api_logs --dry-run              # retention sayımı
```

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

- **`api/urls.py` boş** — tüm endpoint'ler `sais_web/urls.py` içinde tanımlı. Genişlerken `api/urls.py`'ye taşımak temiz olur.
- **API view-app sınır ihlali** — `api/views.py` `sais_domain.SaisCabinet`'i import ediyor (stationId = Bakanlık SIM ID üzerinden filtrelediği için). Bu endpoint'ler (`GetData`, `GetStationInformation`, `StartSample` vb.) aslında SAIS-flavor; ileride `sais_domain`'e (veya yeni bir `sais_api` app'ine) taşımak `api/`'yı tamamen jenerik bırakır.
- **`users` uygulamasının `views.py`'si minimal** — rol bazlı ön yüz akışı ileride eklenecek.
- **Plaintext credentials** — `SaisCabinet.auth_secret` plaintext; `django-fernet-fields` veya bir KMS ile şifrelenmeli (TODO).
- **Per-sensor poll override yok** — `Sensor.poll_interval_sec` alanı modelde var ama dispatcher kullanmıyor; tüm sensörler bağlı oldukları connection'ın periyoduyla okunur. İleride hibrit dispatch eklenebilir.
- **Deadband / change-of-value yok** — `SensorLatest.last_change_at` izlenir ama Reading insert her zaman yapılır; `Sensor.deadband` ile compression ileride.
- **Celery worker monitoring** — Flower kurulu değil; isteğe göre `pip install flower` + `celery -A sais_web flower` ile eklenebilir.
- **Windows'ta lokal Celery** — varsayılan prefork pool Windows'ta sorunlu; `celery -A sais_web worker -P solo -B -l info` veya `eventlet`/`gevent` pool tercih edilmeli.
- **TLS Modbus** — pymodbus 3.x henüz native desteklemiyor; out of scope.
