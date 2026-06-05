# sais_web — Proje Notları

Bu dosya Claude Code için proje rehberidir. Geliştirmeye başlamadan önce okunması önerilir.

## Proje amacı

`sais_web`, **atıksu sürekli izleme** istasyonlarından (SAIS) gelen ölçüm verilerini toplayan, saklayan ve REST API ile sunan bir **web SCADA uygulamasıdır**. Django 5.2 + Django REST Framework üzerine kuruludur.

Mimari üç katmana ayrıldı:

- **`api/`** — jenerik SCADA çekirdeği. Sektör-özel hiçbir kavram içermez. Modeller: `Station`, `StationType`, `Connection`, `ScanGroup`, `Parameter`, `Sensor`, `SensorLatest`, `Reading`, `ReadingFifteenMin`, `ReadingHourly`, `ReadingDaily`, `Calibration`, `PowerOff`, `Command`, `RequestType`, `StatusCode`, `LogType`, `SystemLog`, `ApiLog`.
- **`sais_domain/`** — SAIS'e (Çevre ve Şehircilik Bakanlığı atıksu izleme rejimi) özgü uzantılar. Modeller: `SaisCabinet` (Bakanlık SIM ID + erişim bilgileri), `EnvisoftChannel` (Parameter ↔ Envisoft kanal eşlemesi). Atıksu istasyon tipleri ve "Bakanlık Numune Talebi" gibi alan-özel lookup kayıtları `seed_sais_data` ile yüklenir.
- **`dashboard/`** — Metronic tabanlı rol-bazlı izleme arayüzü. `/dashboard/` URL prefix'i; `/` otomatik oraya yönlendirir. Admin panelinden (Jazzmin) ayrıdır — konfigürasyonu yine admin yapar, dashboard izleme + kısıtlı yönetim içindir. Çoklu dil (TR/EN) Django i18n ile. Ana sayfa 4 canlı widget (KPI + sensör grid + 24s trend + olay akışı), AJAX polling (5-30sn) ile dinamik. Home dışında: 6 rapor + 3 yönetim + 2 operatör + 2 admin sayfası + 3 ayar. Rol mapping: `CustomUser.rol` 1=Sistem Yöneticisi (tam erişim), 2=Operatör (rapor+operatör+yönetim okuma), 3=Normal Kullanıcı (salt-izleme).

Çekirdek özellikler:

- Modbus TCP, Modbus RTU/ASCII (serial), **Modbus RTU/ASCII over TCP** (Teltonika/Moxa gateway transparent mode) ve özel ASCII (request-response) protokol desteği — `Connection.protocol` alanı ile (kapsam kasten Modbus + ASCII ile sınırlı; OPC UA/MQTT/HTTP REST yok)
- **ScanGroup batch reads** (Geo SCADA scanner pattern) — tek Modbus request ile 125 register'a kadar toplu okuma. Tek connection ile 1000+ tag ölçeğinde çalışır.
- **Persistent connection pool** — worker başına 1 socket, polling cycle'ları arasında açık kalır; TCP keep-alive + connection-level-error invalidation ile gateway session pool kilitlenmesini (RUT906, Moxa gibi) önler.
- Çok istasyonlu yapı (`Station` → `Connection` → `ScanGroup` → `Sensor` → `Reading`)
- Parametre/sensör/status şemaları veri modelinde ayrık; sensörde tipli veri (`data_type`, `scale`, `offset`, `decimals`, `bit_position`) ve protokol-özel alanlar (Modbus için slave/function/address, ASCII için request/response/terminator)
- **Reading vs SensorLatest**: snapshot (HMI anlık) ve historian (time-series) ayrı tablolar; `Connection.save_interval_sec` ile polling ≠ kayıt sıklığı ayrışır.
- Aggregate tabloları: `ReadingFifteenMin` / `ReadingHourly` / `ReadingDaily` — dashboard sorgularını raw tablodan kurtarır.
- Komut (write/aksiyon) modeli `Command` — state machine (pending→queued→executing→completed/failed/timeout/expired/cancelled), idempotency, lifecycle timestamps, audit
- Kalibrasyon ve power-off (açılma/kapanma) kayıtları
- Otomatik **API request logging** — gelen ve giden tüm HTTP çağrıları `ApiLog(direction='in'/'out')` olarak kayıt; hassas header/body maskeli; 90 günlük retention
- **Reading retention**: `prune_readings` komutu (90 gün raw, 365 gün 15dk, 5 yıl saatlik, sonsuz günlük default); `prune_api_logs` API log retention.

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
│   ├── models.py          # Station, Connection, ScanGroup, Parameter, Sensor, Reading, Command, ApiLog vs.
│   ├── tasks.py           # aggregate_readings_*, prune_readings_task, prune_api_logs_task (Celery wrap)
│   ├── views.py           # REST API endpoint'leri (bazıları SAIS-flavor; bkz. mimari notu)
│   ├── serializers.py
│   ├── middleware.py      # ApiLoggingMiddleware — gelen istek auto-log
│   ├── api_logging.py     # Outbound helper (log_outbound_call decorator + record_outbound_call)
│   ├── signals.py         # SIGTERM/SIGINT yakalar, PowerOff kaydı kapatır
│   ├── helpers.py         # DataFrame → JSON safe dönüşüm
│   ├── permissions.py
│   ├── management/commands/
│   │   ├── seed_initial_data.py    # Çekirdek seed: Station, Parameter, StatusCode, RequestType
│   │   ├── aggregate_readings.py   # 15m / hour / day bucket hesaplama
│   │   ├── prune_readings.py       # Reading + aggregate retention (raw/15m/hour/day level'lar)
│   │   └── prune_api_logs.py       # 90 günlük ApiLog retention
│   └── migrations/
├── sais_domain/           # SAIS-özel uzantılar (Bakanlık + Envisoft entegrasyonu)
│   ├── models.py          # SaisCabinet, EnvisoftChannel
│   ├── admin.py
│   ├── apps.py
│   ├── management/commands/seed_sais_data.py  # Atıksu StationType + ministry_sample + Envisoft eşlemeleri
│   └── migrations/
├── users/                 # CustomUser (rol tabanlı)
├── dashboard/             # Metronic tabanlı rol-bazlı izleme arayüzü
│   ├── apps.py
│   ├── urls.py            # /dashboard/... URL pattern'leri (namespace="dashboard")
│   ├── views.py           # DashboardLoginView, HomeView, rapor/operatör/admin view'ları
│   ├── api_views.py       # AJAX endpoint'leri — home_kpis/snapshot/trend/events
│   ├── forms.py           # DashboardLoginForm, AdminUserCreate/Update, Profile, ChangePassword
│   ├── permissions.py     # RoleRequiredMixin + AdminRequiredMixin + OperatorRequiredMixin
│   ├── context_processors.py   # MENU dict (rol-filtreli); available_languages
│   ├── templatetags/dashboard_extras.py  # menu_active, has_role, quality_badge filter'ları
│   ├── locale/{tr,en}/LC_MESSAGES/django.{po,mo}    # 178 EN çeviri
│   ├── static/dashboard/  # Metronic asset'leri (~17 MB) — css/js/plugins/fonts/logos/icons
│   ├── templates/dashboard/
│   │   ├── base.html      # light-sidebar iskeleti (auth + header/sidebar/footer ile)
│   │   ├── partials/      # header.html, sidebar.html, footer.html, pagination.html
│   │   ├── auth/          # auth_base.html + login.html + forgot_password.html (corporate layout)
│   │   ├── home.html      # 4 widget + embedded AJAX JS
│   │   ├── reports/       # sensor_readings/aggregates/calibrations/power_offs/commands/system_logs
│   │   ├── operator/      # sample_trigger, alarms
│   │   ├── management/    # stations, connections, sensors (readonly)
│   │   ├── admin_pages/   # user_list, user_form, api_logs (rol=1 only)
│   │   ├── settings/      # profile, change_password, preferences
│   │   └── errors/        # 403, 404 (placeholder)
│   └── migrations/
├── scada_io/              # SCADA reader/writer çekirdeği (Modbus + ASCII)
│   ├── decoders.py        # decode_registers / decode_sensor_from_batch / encode_value (12 data_type × byte/word combos)
│   ├── persistence.py     # persist_reading — Reading insert + SensorLatest upsert (save_interval_sec gate + decimals)
│   ├── connection_pool.py # Worker-local persistent socket pool + TCP keep-alive
│   ├── readers/           # ProtocolReader: base / modbus_tcp / modbus_serial / ascii_custom + factory
│   │                      # `read_raw()` abstract — ScanGroup batch read için
│   ├── writers/           # ProtocolWriter: base / modbus_tcp / modbus_serial / ascii_custom + factory
│   ├── tasks.py           # Celery: dispatch_polls / poll_connection / dispatch_commands / execute_command / expire_commands
│   ├── tests.py           # Decoder unit tests (12 data_type × byte/word combos)
│   └── management/commands/
│       ├── seed_periodic_tasks.py   # django_celery_beat PeriodicTask kayıtları (8 task)
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
python manage.py seed_periodic_tasks  # 9 Celery beat periodic task (idempotent)
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

- **Yapılandırma:** Tüm ayarlar `.env` üzerinden okunur (`python-dotenv`). Sırları asla koda commitlemeyin. Ek env'ler: `API_LOG_*`, `READING_RETENTION_*_DAYS`, `CELERY_BROKER_URL` (default `redis://localhost:6379/2`), `CELERY_RESULT_BACKEND` (default `django-db`).
- **`USE_TZ=True` zorunlu** — django-celery-beat 2.9 + Celery 5.6 kombinasyonu USE_TZ=False'ta sessizce task tetiklemiyor (`is_due()` timezone karşılaştırmasında hata). Default `.env` `DJANGO_USE_TZ=1`; değiştirme.
- **DEBUG=False** modunda HSTS, güvenli çerez ve Whitenoise manifest storage aktiftir.
- **Seed komutları idempotenttir** — `seed_initial_data`, `seed_sais_data`, `seed_periodic_tasks` birden çok kez çalıştırmak güvenlidir (`get_or_create` / `update_or_create`).
- **pymodbus 3.11+ API:** readers/writers `device_id=` parametresi kullanır (pymodbus 3.13'te `slave=` → `device_id=` rename edildi). Bu dokümantasyonda veya PR'da `slave=` görürsen o eski API'dir.
- **SCADA polling akışı:** `scada_io.tasks.dispatch_polls` her 5 sn'de çalışır; `Connection.last_polled_at + poll_interval_sec` due olan bağlantılar için `poll_connection.delay(conn_id)` enqueue eder. Worker pool'dan cached reader alır (veya yeni bağlantı açar):
  1. Connection'ın aktif `ScanGroup`'larını **tek Modbus request** ile okur (`reader.read_raw`), ham register listelerini bellekte tutar.
  2. Her sensör için: `scan_group` varsa batch cache'inden `decode_sensor_from_batch` ile değer çıkar; yoksa legacy `reader.read(sensor)` ile tek-tek okur.
  3. `persist_reading` ile Reading insert + SensorLatest upsert.
  Reader factory `Connection.protocol` bazlı: `modbus_tcp` (MBAP), `modbus_rtu_over_tcp` / `modbus_ascii_over_tcp` (gateway transparent mode — RTU/ASCII frame TCP socket üzerinden), `modbus_rtu` / `modbus_ascii` (serial port), `ascii_custom` (pyserial request-response).
- **Persistent connection pool** (`scada_io.connection_pool`): Worker process'e özel, module-level socket cache. Her polling'de open/close yerine aynı socket polling cycle'ları boyunca açık kalır. RUT906/Moxa gibi gateway'lerde session pool kilitlenmesini önler. Connection-level hata ("bağlantı yok/broken/reset/socket/disconnected") tespit edilince `invalidate()` — sonraki cycle'da taze socket. 3 peş peşe başarısız cycle'da watchdog invalidate. TCP keep-alive (Linux: 60s/10s/3 probe; Windows: SO_KEEPALIVE + OS default).
- **ScanGroup batch reads (Geo SCADA scanner pattern):** Bir `Connection` üzerinde çoklu `ScanGroup` (slave_id + function + start_address + quantity) tanımlanır. Her grup tek Modbus request'te okunur; grup'a bağlı tüm `Sensor`'lar o batch'ten offset ile decode edilir. **Quantity hard-enforce**: function 3/4 için max 125, function 1/2 için max 2000. `Sensor.address` scan_group aralığında olmalı (clean() validation). `scan_group=None` sensörler legacy per-sensor read mod'unda kalır (geri uyumlu).
- **Sensör decode:** `scada_io.decoders.decode_registers` 12 data_type (int16/uint16/int32/uint32/int64/uint64/float32/float64/bool/bit/string/raw) × byte_order × word_order kombinasyonlarını destekler. `Sensor.scale * raw + Sensor.offset` mühendislik dönüşümü uygulanır; `Sensor.decimals` verilmişse `round(value, decimals)` ile float yuvarlaması. Bit-okuma: register içindeki `bit_position`. ASCII: `ascii_request` gönder + `ascii_response_regex` ile değer çıkar.
- **Sensör simülasyonu:** `Sensor.is_simulated=True` ise reader cihaza dokunmaz; `scada_io.tasks._record_simulated` `parameter.min_range`/`max_range` arası rastgele değer üretir (`Reading.origin='simulated'`).
- **Reading vs SensorLatest + save_interval_sec:** `SensorLatest` per-sensor snapshot (HMI anlık); `Reading` time-series historian. `persist_reading` snapshot'ı **her polling'de** günceller, ama `Reading` insert'i `Connection.save_interval_sec` ile gated: null ise her polling'de; değer verilmişse sensör başına o sürede bir insert (DB şişmesini önler). Tipik: poll_interval=10sn, save_interval=60sn → HMI 10sn'de taze, historian dakikada 1 satır. `SensorLatest.last_change_at` değer değiştiğinde, `last_saved_at` Reading insert'inde, `update_count` her snapshot'ta güncellenir.
- **Aggregate tabloları:** `ReadingFifteenMin` / `ReadingHourly` / `ReadingDaily` — sensör başına bucket avg/min/max/count/bad_count. Dashboard'lar bunları sorgulasın, raw `Reading`'i taramasın. `aggregate_readings` komutu (manuel) veya `api.tasks.aggregate_readings_*` Celery task'larıyla (otomatik) doldurulur.
- **Komut tetikleme:** `StartSampleView` → `Command` tablosuna `idempotency_key='ministry_sample:{station}:{code}'` ile kayıt; `priority=10`, `expires_at=+5dk`. `scada_io.tasks.dispatch_commands` her 3 sn'de pending komutları atomik `pending → queued` yapıp `execute_command.delay(cmd_id)` enqueue eder. Worker writer açar, yazar, status `executing → completed/failed`. Başarısızsa `attempt_count < max_attempts` iken `pending`'e geri döner (otomatik retry); `expire_commands` her 60 sn'de süresi dolanları `expired` yapar.
- **API request logging:** `api.middleware.ApiLoggingMiddleware` her gelen isteği `ApiLog(direction='in')` olarak kaydeder; süre ölçer, `request.user`/IP/user-agent yakalar, hassas header (`Authorization`/`Cookie`/`X-API-Key`) ve body key'leri (`password`/`secret`/`token`/`api_key`) maskeli. Skip path'ler: `/static/`, `/media/`, `/__debug__/`, `/admin/jsi18n/`, `/favicon.ico`. Giden HTTP çağrıları için `api.api_logging.log_outbound_call` decorator veya `record_outbound_call(...)` helper kullanılır.
- **Retention:**
  - `prune_api_logs` (`API_LOG_RETENTION_DAYS`, default 90gün) — günlük cron.
  - `prune_readings` 4 seviye (`READING_RETENTION_RAW_DAYS=90`, `..._15M_DAYS=365`, `..._HOURLY_DAYS=1825`, `..._DAILY_DAYS=99999`) — günlük cron. Batch delete (10K/transaction) ile MSSQL tek büyük transaction'dan kaçınır.
- **Dashboard arayüz** (`/dashboard/`): Metronic 8.2 tabanlı light-sidebar layout. Giriş `/dashboard/login/` (Metronic corporate template, sosyal login/signup yok); "şifremi unuttum" admin'e yönlendiren info sayfası. Ana sayfa 4 widget'lı (KPI/grid/trend/events) AJAX polling ile canlı. Role mapping: rol=1 (admin) her menüyü görür, rol=2 (operatör) admin dışı, rol=3 (user) salt-izleme (komut/sistem log yok). Session cookie 24h (`SESSION_COOKIE_AGE=86400`); "Beni hatırla" işaretlenirse 30 gün.
- **Dil desteği (i18n):** `USE_I18N=True`, `LANGUAGES=[("tr","Türkçe"),("en","English")]`, `LOCALE_PATHS=[dashboard/locale]`. TR default, EN çevirisi `dashboard/locale/en/LC_MESSAGES/django.po` (178 entry). `django.mo` dosyası commit'te; gettext binary olmadan Python script ile compile edildi. Yeni string eklendiğinde ya Linux/Docker'da `python manage.py compilemessages` ya da `_compile_po.py` benzeri bir script kullanılmalı (Windows gettext eksik).

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
| `api.tasks.prune_readings_task` | `30 2 * * *` | Reading + aggregate retention'ı geçenleri sil |
| `api.tasks.prune_api_logs_task` | `0 3 * * *` | `API_LOG_RETENTION_DAYS`'den eski kayıtları sil |
| `sais_domain.tasks.publish_minute_data` | `* * * * *` | Aktif kabinler için SIM + Envisoft `SendData` fan-out |

Yönetim:
- Admin panelinden (`/admin/django_celery_beat/periodictask/`) bireysel task'lar enable/disable edilebilir veya periyot değiştirilebilir.
- Yeni task eklenirse `seed_periodic_tasks` listesine ekle ve yeniden çalıştır (idempotent).

Manuel çalıştırma (debug):
```bash
python manage.py poll_once <connection_id>             # tek bağlantıyı sync polla
python manage.py execute_command_now <command_id>      # tek komutu sync yürüt
python manage.py aggregate_readings --bucket=15m       # aggregate'i manuel tetikle
python manage.py prune_readings --level=raw --dry-run  # Reading retention sayımı
python manage.py prune_readings --level=raw --days=30  # override ile sil
python manage.py prune_api_logs --dry-run              # ApiLog retention sayımı
```

## Kod düzenleme kuralları

- Mevcut dosyaların biçim stiline sadık kal (Türkçe verbose_name/help_text, snake_case tablo adları).
- **App sınırını koru:** jenerik SCADA kavramları `api/`'a, SAIS/Bakanlık/Envisoft özelinde olanlar `sais_domain/`'e gider. Yeni bir model/seed/komut eklerken hangi tarafa ait olduğunu sor.
- Veritabanı şema değişikliklerinde `python manage.py makemigrations` + `migrate` üret. MSSQL'de bazı manuel migration'lar (`SeparateDatabaseAndState`) gerekebilir; bkz. `api/migrations/0006_apilog_rebuild.py`.
- Yeni secret/ayar eklerken hem `.env.example` hem `settings.py`'yi güncelle.
- `api/views.py` — **Response sözleşmesi** tüm endpoint'lerde aynı: `{"result": bool, "message": str | null, "objects": any}`. Yeni endpoint eklerken bu formatı koru.
- REST endpoint default `IsAuthenticated`; public uç nokta eklenirken `permission_classes` açıkça override edilmeli.
- Dış HTTP çağrılarını `api.api_logging.log_outbound_call(component="...")` decorator'u veya `record_outbound_call(...)` helper'ı ile sar — `ApiLog`'da `direction='out'` kaydı otomatik yazılsın.
- **Reader/writer protokol eklerken**: `scada_io/readers/` ve `scada_io/writers/` altında hem `base.ProtocolReader/Writer` subclass'ı hem `factory.py`'ye mapping ekle. Mevcut pattern: open/read/close + context manager; read_raw abstract (batch read için). Reader stateless olmalı; persistence `scada_io/persistence.py`'den çağrılır.
- **Yeni Celery task eklerken**: `api/tasks.py` (Celery wrap — management komutunu çağırır) veya `scada_io/tasks.py` (asıl I/O mantığı). Periyodik tetikleme için `scada_io/management/commands/seed_periodic_tasks.py` içindeki `INTERVAL_TASKS` / `CRONTAB_TASKS` listesine ekle ve yeniden seed çalıştır.
- **pymodbus 3.13+ API:** read_coils/holding_registers/input_registers/discrete_inputs ve write_coil/register'da `device_id=` parametresi kullanılır; eski `slave=` kabul edilmez.
- `pyproject.toml`/`ruff`/`black` gibi linter yok — mevcut stili el ile koru.

## Dashboard rapor sayfaları — standart iskelet

`/dashboard/reports/` altındaki rapor sayfalarının standardı `dashboard/templates/dashboard/reports/sensor_readings.html` + `ReadingsReportView` (`dashboard/views.py`) + `station_parameters` (`dashboard/api_views.py`) ile belirlenmiştir. Yeni rapor sayfası (`/dashboard/reports/<x>/`) eklerken aşağıdaki desenleri **birebir uygula**.

### View (ListView)

- `class XxxReportView(RoleRequiredMixin, ListView)` — rol 3 dahil herkes; salt-operatör için `OperatorRequiredMixin`.
- `paginate_by = None` (DataTables client-side handle eder); `MAX_ROWS = 50000` hard limit ile queryset'i slice et (`qs.order_by(...)[: self.MAX_ROWS]`).
- `DATE_FORMAT = "%d.%m.%Y %H:%M:%S"` + `_parse_dt(raw)` helper'ı TZ-aware datetime döndürsün (`timezone.make_aware`).
- `INTERVAL_CHOICES` ile bucket seçimi: `1min→Reading`, `15min→ReadingFifteenMin`, `hourly→ReadingHourly`, `daily→ReadingDaily`. Raw için `time_iso`, aggregate'lerde `bucket_start` filtre kullan.
- `_filters()` method'u GET parametrelerini normalize edip dict döndürsün (`submitted`, `station_id`, `parameter_ids`, `status_ids`, `interval`, `start`, `end`, `chart`).
- `get_queryset`: `submitted=False` veya `station_id=None` ise `Model.objects.none()`.
- **Parametre kapsamı**: `Parameter.objects.filter(sensors__connection__station_id=X).distinct()`. `Parameter.station` FK'sı **güvenilir değil** — her zaman sensörlerden git. Aynı kural [`station_parameters`](dashboard/api_views.py) AJAX endpoint için de geçerli.
- Parametre grup ataması (sensors[0].sensor_type): `0/1 → Analog`, `2/3 → Dijital`, başka/yok → `Diğer`. `parameters_analog/digital/other` context'e koy.
- `get_context_data` çıktısı: `filters, is_raw, interval_choices, stations, status_codes, parameters_analog/digital/other`.

### Template iskeleti

İki kart: **Filtre kartı** + **Rapor Çıktısı kartı**.

- `{% extends "dashboard/base.html" %}` + `{% load i18n static dashboard_extras %}`.
- `{% block title %}` ve `{% block page_title %}` aynı i18n stringi versin.
- Filtre form: `<form method="get" id="report-filter-form" class="row g-5">`, GET submit.
- **İstasyon Seçimi** (col-md-6): `data-control="select2" data-allow-clear="true"`, ilk option boş.
- **Parametre Listesi** (col-md-6): `multi + data-control="select2"` + optgroup `Analog/Dijital/Diğer`; istasyon yoksa `disabled`. Altta hint metni: "Önce bir istasyon seçin." → seçim sonrası "Parametre seçilmezse o istasyonun tüm parametreleri gösterilir.".
- **Veri Aralığı** (col-md-3): `interval_choices` dropdown.
- **Status Filtresi** (col-md-3, multi `StatusCode`): sadece raw için uygulanır, aggregate'lerde göz ardı.
- **Başlangıç / Bitiş Tarihi** (col-md-3 + col-md-3): flatpickr inline TR locale (`flatpickrTR` object); format `d.m.Y H:i:S`. Altlarında `<div class="invalid-feedback">`.
- **Form-seviyesi alert**: `<div id="form-alert" class="alert alert-warning d-none">`.
- **Rapor Getir**: `id="btn-generate" class="btn btn-primary"`; istasyon yoksa `disabled`. **Sıfırla**: `btn-light-warning` (yalnız `filters.submitted` iken).
- **Rapor Çıktısı kartı**: `id="report-results-card"` + card-toolbar'da "Grafik Oluştur" toggle switch. Boş durumda "rapor oluşturmak için istasyon seçip…" mesajı. Tablo wrapper `id="report-table-wrap"`, grafik wrapper `id="report-chart-wrap"` (default `display:none`).
- **Tablo**: `<table id="report-table" class="table table-row-bordered table-row-gray-300 align-middle gs-3 gy-3 w-100">`. Sıralanacak sütunlarda `<td data-order="...">` (Unix timestamp, raw float).

### JS — validasyon

`validateForm()` sayfa load + her input/change/flatpickr `onChange`/`onClose`'da çağrılsın:

- Tarih: boş / format hatalı (`gg.aa.yyyy SS:DD:SS` regex) / başlangıç > bitiş.
- Hata varsa: input'a `is-invalid` class + `invalid-feedback` mesaj + form üstü alert.
- İstasyon yoksa hata listesine ekle.
- Hatalı durumda **Rapor Getir disabled** + submit handler `e.preventDefault()` (Enter ile bypass'ı engelle).

### JS — AJAX dinamik parametre yenileme

- Endpoint: `dashboard:api_station_parameters` (`/dashboard/api/parameters/?station=<id>`).
- JSON kontratı: `{"results": [{"text": "Analog", "children": [{id, text, unit}, ...]}]}`.
- Frontend: yanıt geldikten sonra **select2 destroy + reinit**:

```js
function reinitParamSelect2() {
    if ($paramSel.hasClass('select2-hidden-accessible')) $paramSel.select2('destroy');
    $paramSel.select2(PARAM_S2_OPTS);
}
```

Metronic'in `data-control="select2"` auto-init'i sonradan DOM'a eklenen `<option>`'ları görmüyor — `trigger('change')` yetmez.
- İstasyon değiştiğinde: select empty → AJAX → optgroup'ları doldur → `setParamDisabled(false)` + `reinitParamSelect2()` + `validateForm()`.
- İstasyon temizlenince: select empty + `disabled` + hint metnini sıfırla.

### DataTables init

- Client-side (server-side pagination YOK).
- `dom`: `Buttons (B) + length (l) + filter (f) + table (t) + info (i) + paginate (p)` pattern (bkz. `sensor_readings.html`).
- Buttons: `copyHtml5, csvHtml5, excelHtml5, pdfHtml5, print, colvis` + KI ikon + Metronic `btn-sm` color class'ları:
  - Copy → `btn-light-primary`, CSV → `btn-light-primary`, Excel → `btn-light-success`, PDF → `btn-light-danger`, Print → `btn-light-info`, Colvis → `btn-light`.
- `pageLength: 10`, `lengthMenu: [[10,25,50,100,-1], [10,25,50,100,'Hepsi']]`, `order: [[0,'desc']]`.
- `language` TR `{% trans %}` ile besle (lengthMenu, search, paginate, info, zeroRecords, emptyTable).

### ApexCharts grafik

- "Grafik Oluştur" toggle: açılınca `tableWrap.style.display='none'`, `chartWrap.style.display='block'`, render. Kapanınca tersi + `dt.columns.adjust()`.
- Veri kaynağı: `#report-table tbody tr` cells[0]=zaman, cells[1]=parametre, cells[2]=değer; regex `/(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2})(?::(\d{2}))?/`.
- Tema dinamik: `isDark = document.documentElement.getAttribute('data-bs-theme') === 'dark'`. Light/dark için `axisColor`, `gridColor`, `theme.mode`, `tooltip.theme` ayrı.
- 10 renkli palette (Metronic primary/success/danger/warning/info ekseninde).
- `chart.toolbar.tools` tümü açık, `autoSelected: 'zoom'`.
- `stroke.width: 3 + lineCap:'round'`, `markers.hover.size: 6`, `grid.strokeDashArray: 4`.
- Re-render için **`chartInstance.destroy()` + yeni `ApexCharts(...)`** (updateOptions yerine tam destroy/reinit — light↔dark geçişlerinde tutarlılık).
- Toolbar ikonlarının default Apex SVG'leri silik kalır; CSS override ile `fill: var(--bs-gray-700)` → hover/`selected` `var(--bs-primary)`.

### FOUC önleme

Rapor sayfası submit'i sonrası DataTables/select2/flatpickr/Apex init zinciri tamamlanana dek "atlama" oluşuyor. Sayfa-spesifik patch:

```css
#kt_app_content_container { opacity: 0; }
body.page-ready #kt_app_content_container {
    opacity: 1;
    transition: opacity 0.18s ease-in-out;
}
```

```js
function revealContent() {
    if (document.body.classList.contains('page-ready')) return;
    requestAnimationFrame(() => document.body.classList.add('page-ready'));
}
if (document.readyState === 'complete') revealContent();
else window.addEventListener('load', revealContent);
setTimeout(revealContent, 1200);   // failsafe
```

### i18n

- Tüm UI metinleri `{% trans %}`; JS string'leri için `const I18N = { key: "{% trans 'X' %}" };` paterni.
- DataTables `language` config'i de `{% trans %}` ile.
- Yeni string → `dashboard/locale/en/LC_MESSAGES/django.po`'ya çeviri ekle, ardından `.mo` derle. Windows'ta gettext yok; mevcut pure-Python msgfmt scripti ile derliyoruz (önceki bash bloğunda kullanıldı — kabaca `parse_po` + GNU MO struct yaz).

### URL routing

- View → `dashboard/views.py`; URL → `dashboard/urls.py` (namespace `dashboard`); pattern: `path("reports/<x>/", views.XxxReportView.as_view(), name="reports_<x>")`.
- AJAX endpoint'leri `dashboard/api_views.py` + `login_required`; URL prefix `/dashboard/api/...`.

### Pagination yerine querystring koruma

Sayfalama yapan tablolarda `{% querystring_without "page" %}` template tag'i ile mevcut filtre parametrelerini link'lerde koru.

### Buton renkleri (SCADA paleti)

- Action (Rapor Getir / Submit): `btn-primary`.
- Reset / ikincil: `btn-light-warning` veya `btn-light`.
- DataTables export buton renkleri yukarıda.

### Container

- Tüm dashboard sayfaları `container-fluid` ([`base.html`](dashboard/templates/dashboard/base.html)'de set edildi). `container-xxl` kullanma — geniş ekranda iki yanda boşluk açar.

> **Yeni rapor sayfası eklerken**: `sensor_readings.html` + `ReadingsReportView` referans alınmalı; üst başlık, filtre alanları, validasyon, DataTables, grafik, FOUC blocklarının tamamı kopyalanıp adapte edilmeli — kullanıcı bu sayfayla "rapor altyapısını öğrendik" diye onayladı, bu standart artık her rapor sayfasında beklenmeli.

## Testler

`api/tests.py`, `users/tests.py`, `modbus/tests.py` şu an boş. Yeni özellik eklerken ilgili uygulamaya test yazılması beklenir.

```bash
python manage.py test
```

## Git & commit kuralları

- Master branch: `master`. PR hedefi: `main` (GitHub'da).
- Bu projede **her mantıksal değişiklikten sonra otomatik commit + push** yapılır (kullanıcı isteği). `.env`, DB şifreleri, `venv/`, `media/` gibi dosyalar commitlenmez.
- Commit mesajları Conventional Commits: `feat:`, `fix:`, `chore:`, `docs:`, `refactor:` prefiksleri tercih edilir.

## Kapasite notu

Mevcut mimarinin test edilmiş + hedeflenen kapasitesi:

| Ölçek | Durum | Gereken ek iş |
|---|---|---|
| 0–500 tag | ✅ Hazır | — |
| 500–1500 tag | ✅ Hazır | Retention (zaten `prune_readings` var), `save_interval_sec=60` önerilir |
| 1500–3000 tag | ⚠️ Tuning | + SQL-side aggregate rewrite (Python groupby yavaşlar) |
| 3000–5000 tag | ⚠️ Multi-connection | + DB partition (aylık Reading partition), Celery concurrency artır |
| 5000+ | ❌ Mimari değişiklik | TimescaleDB/InfluxDB historian gerekir |

**1000 tag için önerilen konfigürasyon:**
- 10-15 ScanGroup (register haritasına göre bloklar; her biri 50-100 sensör)
- `Connection.poll_interval_sec=10`, `Connection.save_interval_sec=60`
- `Sensor.decimals=2` (float sensörlerde)
- Docker `--concurrency=4` (solo dev için `-P solo`)

Beklenen yük: cycle 1.5-2.5 sn, 17 Reading insert/sn, ~1.4M satır/gün, 90 gün retention → ~130M satır (indexli, query hızlı).

## Bilinen sınırlamalar / yol haritası

- **`api/urls.py` boş** — tüm endpoint'ler `sais_web/urls.py` içinde tanımlı. Genişlerken `api/urls.py`'ye taşımak temiz olur.
- **API view-app sınır ihlali** — `api/views.py` `sais_domain.SaisCabinet`'i import ediyor (stationId = Bakanlık SIM ID üzerinden filtrelediği için). Bu endpoint'ler (`GetData`, `GetStationInformation`, `StartSample` vb.) aslında SAIS-flavor; ileride `sais_domain`'e (veya yeni bir `sais_api` app'ine) taşımak `api/`'yı tamamen jenerik bırakır.
- **`users` uygulamasının `views.py`'si minimal** — rol bazlı ön yüz akışı ileride eklenecek.
- **Plaintext credentials** — `SaisCabinet.auth_secret` plaintext; `django-fernet-fields` veya bir KMS ile şifrelenmeli (TODO).
- **Per-sensor poll override yok** — `Sensor.poll_interval_sec` alanı modelde var ama dispatcher kullanmıyor; tüm sensörler bağlı oldukları connection'ın periyoduyla okunur. İleride hibrit dispatch eklenebilir.
- **Deadband / change-of-value yok** — `SensorLatest.last_change_at` izlenir ama Reading insert her zaman yapılır (save_interval_sec gate hariç); `Sensor.deadband` ile compression ileride.
- **Aggregate Python-side** — `aggregate_readings` pandas-benzeri Python groupby kullanır; 1500+ tag ölçeğinde MSSQL `GROUP BY` SQL rewrite gerekir.
- **DB partition yok** — `Reading` tek tablo; 3000+ tag uzun vadeli operasyonda aylık partition (MSSQL partitioned tables) gerekir.
- **Celery worker monitoring** — Flower kurulu değil; isteğe göre `pip install flower` + `celery -A sais_web flower` ile eklenebilir.
- **Windows'ta lokal Celery** — `-B` (worker+beat tek process) desteklenmiyor; iki ayrı terminal aç veya `-P solo` ile worker + ayrı terminal'de beat. Prefork pool Windows'ta sorunlu, `-P solo` zorunlu.
- **TLS Modbus** — pymodbus 3.x henüz native desteklemiyor; out of scope.
