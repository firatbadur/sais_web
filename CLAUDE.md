# sais_web — Proje Notları

Bu dosya Claude Code için proje rehberidir. Geliştirmeye başlamadan önce okunması önerilir.

> **CLAUDE.md'yi güncel tut (ZORUNLU).** Sisteme **yeni bir özellik**, **mimari değişiklik** veya
> önemli/somut bir şey (yeni model, app, Celery task, protokol, dashboard sayfası, çalıştırma
> davranışı, env ayarı, dağıtım/installer adımı vb.) eklendiğinde — değişiklikle **aynı turda** —
> bu dosyaya kısa bir özet işle. İlgili bölüme (mimari, "Önemli çalıştırma davranışları", periyodik
> task tablosu, proje yapısı ağacı, kod düzenleme kuralları, bilinen sınırlamalar...) ekle; uygun
> bölüm yoksa yeni bir `##` başlık aç. Özet **niyeti + tek doğruluk kaynağını** (model/dosya/komut)
> versin, satır satır diff değil. Sadece kozmetik/küçük düzeltmeler için güncelleme gerekmez.

> **Ürün/marka adı: "Envisoft WebX".** Kullanıcı-görünür her yerde (dashboard UI, admin başlıkları,
> Windows installer, masaüstü/başlat menüsü kısayolları, kurulum dizini `C:\EnvisoftWebX`, Windows
> servisi `EnvisoftWebX`) bu ad kullanılır. **Kod adı `sais_web` ve iç tanımlayıcılar (app `sais_domain`,
> tablo `sais_*`, GHCR image `sais_web`, komut `seed_sais_data`, `SAIS_SIM_*` Bakanlık API'leri)
> DEĞİŞMEDİ** — onlar teknik isimler / Bakanlık SAIS rejimine ait, marka değil.

## Proje amacı

`sais_web` (ürün adı **Envisoft WebX**), **atıksu sürekli izleme** istasyonlarından (SAIS — Çevre
Bakanlığı rejimi) gelen ölçüm verilerini toplayan, saklayan ve REST API ile sunan bir **web SCADA
uygulamasıdır**. Django 5.2 + Django REST Framework üzerine kuruludur.

Mimari üç katmana ayrıldı:

- **`api/`** — jenerik SCADA çekirdeği. Sektör-özel hiçbir kavram içermez. Modeller: `Station`, `StationType`, `Connection`, `ScanGroup`, `Parameter`, `Sensor`, `SensorLatest`, `Reading`, `ReadingFifteenMin`, `ReadingHourly`, `ReadingDaily`, `Calibration`, `PowerOff`, `Command`, `RequestType`, `StatusCode`, `LogType`, `SystemLog`, `ApiLog`.
- **`sais_domain/`** — SAIS'e (Çevre ve Şehircilik Bakanlığı atıksu izleme rejimi) özgü uzantılar. Modeller: `SaisCabinet` (Bakanlık SIM ID + erişim bilgileri), `EnvisoftChannel` (Parameter ↔ Envisoft kanal eşlemesi). Atıksu istasyon tipleri ve "Bakanlık Numune Talebi" gibi alan-özel lookup kayıtları `seed_sais_data` ile yüklenir.
- **`dashboard/`** — Metronic tabanlı rol-bazlı izleme arayüzü. `/dashboard/` URL prefix'i; `/` otomatik oraya yönlendirir. Admin panelinden (Jazzmin) ayrıdır — konfigürasyonu yine admin yapar, dashboard izleme + kısıtlı yönetim içindir. Çoklu dil (TR/EN) Django i18n ile. Ana sayfa 4 canlı widget (KPI + sensör grid + 24s trend + olay akışı), AJAX polling (5-30sn) ile dinamik. Home dışında: 7 rapor + 3 yönetim + 2 operatör + 2 admin sayfası + 3 ayar. Rol mapping: `CustomUser.rol` 1=Sistem Yöneticisi (tam erişim), 2=Operatör (rapor+operatör+yönetim okuma), 3=Normal Kullanıcı (salt-izleme).

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
| Veritabanı | PostgreSQL 16 (psycopg 3) |
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
│   ├── web_proxy.py       # WebSettings → Caddyfile render + PFX→PEM + atomik yazım
│   ├── helpers.py         # DataFrame → JSON safe dönüşüm
│   ├── permissions.py
│   ├── management/commands/
│   │   ├── seed_initial_data.py    # Çekirdek seed: Station, Parameter, StatusCode, RequestType
│   │   ├── aggregate_readings.py   # 15m / hour / day bucket hesaplama
│   │   ├── prune_readings.py       # Reading + aggregate retention (raw/15m/hour/day level'lar)
│   │   ├── prune_api_logs.py       # 90 günlük ApiLog retention
│   │   ├── detect_power_off.py     # Açılışta heartbeat boşluğundan PowerOff kaydı düşer
│   │   └── render_caddyfile.py     # WebSettings → Caddyfile üret (startup + panel save)
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
│   │   ├── reports/       # sensor_readings(ham)/data_report(pivot)/aggregates/calibrations/power_offs/commands/system_logs
│   │   ├── operator/      # scenario_builder (numune senaryosu — 4 sekme), alarms
│   │   ├── management/    # stations, connections, sensors (readonly)
│   │   ├── admin_pages/   # user_list, user_form, api_logs, web_settings (rol=1 only)
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
├── users/management/commands/seed_admin_user.py  # non-interactive rol=1 admin (installer)
├── installer/             # Windows "next-next-next" kurulum paketi (Inno Setup)
│   ├── sais_setup.iss     # Sihirbaz + gömülü GHCR token (build-time) + reboot/RunOnce
│   ├── templates/env.template          # .env şablonu (installer doldurur)
│   ├── scripts/           # install (orkestratör) + 00-ensure-docker..40-register-service
│   │                      # + sais-stack (NSSM) + uninstall + _common (WSL Docker)
│   └── payload/           # nssm.exe (build'de çekilir, commit'lenmez)
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
python manage.py seed_periodic_tasks  # Celery beat periodic task'lar (idempotent)
python manage.py seed_report_templates  # Rapor Stüdyosu yerleşik "Günlük Tesis Özeti" şablonu
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

Servisler: `web` (Django), `celery_worker`, `celery_beat`, `db` (PostgreSQL 16), `redis` (Redis 7).

> **Yerel geliştirme ön koşulu:** Bir PostgreSQL sunucusu erişilebilir olmalı. En kolayı `docker compose up -d db` ile bundled `postgres:16` container'ını kaldırmak; alternatif olarak hostta yerel PostgreSQL kurup `.env`'deki `POSTGRES_*` alanlarını ona göre ayarlamak. `psycopg[binary]` Python tarafında ek sistem bağımlılığı (ODBC/libpq derleme) gerektirmez.

## Filo dağıtımı / sürüm güncelleme (çok-sahalı)

SCADA birçok atıksu istasyonuna kurulur. Güncellemeler **pull-based** dağıtılır: sahalar
NAT/firewall arkasında, içeri erişim yok ama internete çıkıyorlar (zaten Bakanlık SIM'e veri
gönderiyorlar). İki compose dosyası var:

- `docker-compose.yml` — **dev**, yerel `build:`. Değiştirme.
- `docker-compose.prod.yml` — **saha**, GHCR'dan `image:` çeker + `watchtower` servisi.

**Mimari:** `git tag vX.Y.Z` push → GitHub Actions ([.github/workflows/release.yml](.github/workflows/release.yml))
multi-stage build → `ghcr.io/firatbadur/sais_web` iki tag ile:
`:vX.Y.Z` (değişmez, rollback) + `:stable` (kayan). Her sahadaki Watchtower `:stable` etiketli
**app** container'larını (web/worker/beat — `com.centurylinklabs.watchtower.enable=true` label'lı)
günceller. **Otomatik periyodik tarama YOK** (`WATCHTOWER_HTTP_API_UPDATE=true` → poll döngüsü
kapalı, `WATCHTOWER_POLL_INTERVAL`/`--schedule` tanımlı değil): tag push'lamak sahalarda hiçbir
şeyi otomatik tetiklemez, yalnızca GHCR'a yeni image basar. Güncelleme **manuel** — dashboard
"Şimdi Güncelle" butonu Watchtower HTTP API'sini (`WATCHTOWER_API_TOKEN` ile) çağırınca yeni digest
çekilir + container recreate edilir. `db` (PostgreSQL) ve `redis` **label'sız → asla güncellenmez**,
volume'leri korunur.

- **Saha kimliği state'tir, image değil.** Station / Connection / Sensor / **SaisCabinet (Bakanlık
  SIM ID)** / SystemSwitch → DB volume'de; `.env` → hostta lokal. Güncelleme bunlara dokunmaz.
- **Migration yarışı:** `web` `migrate` çalıştırır; `celery_worker`/`celery_beat` komutları başta
  `until python manage.py migrate --check; do sleep 3; done` ile şema hazır olana dek bekler
  (yeni kod eski şemayla çalışmasın).
- **Sürüm görünürlüğü:** Dockerfile `ARG APP_VERSION` → `settings.APP_VERSION` → dashboard footer
  badge. Lokal/dev'de "dev". Hangi sahanın hangi sürümde olduğunu footer'dan gör.

**Sürüm çıkarma (tek komut, tüm filo):**
```bash
git tag v1.2.0 && git push origin v1.2.0   # Actions build+push :stable → GHCR'da hazır; sahalar dashboard "Şimdi Güncelle" ile çeker
```
**Rollback / dondurma (tek saha):** o sahanın `.env`'inde `IMAGE_TAG=v1.1.0` → `docker compose -f
docker-compose.prod.yml --env-file .env up -d`.

**Yeni saha açma:** [scripts/bootstrap-site.ps1](scripts/bootstrap-site.ps1) (Windows) /
[scripts/bootstrap-site.sh](scripts/bootstrap-site.sh) (Linux) — `docker login ghcr.io`
(read:packages PAT) + `pull` + `up -d`; `-FirstRun`/`FIRST_RUN=1` ile bir kez
`seed_initial_data` + `seed_sais_data` + superuser. Sonra admin'den saha-özel kayıtlar girilir.

İlgili `.env` değişkenleri: `GHCR_IMAGE`, `IMAGE_TAG`, `WATCHTOWER_API_TOKEN` (dashboard "Şimdi Güncelle" HTTP API token'ı), `WEB_PORT`.

## Veritabanı yedekleme / geri yükleme (sürüm-bilinçli)

`pg_dump -Fc` (custom format) ile tam yedek (.dump), `pg_restore` ile geri yükleme. Generic SCADA
altyapısı → `api/`. Dosyalar paylaşımlı `pg_backups` volume'ünde (`settings.BACKUP_DIR`, `/backups`);
`web` + `celery_worker` container'larına mount. **pg_dump'ı app container çalıştırır** (binary imajda,
`postgresql-client-16`) → dosyayı doğrudan app yazar, izin sorunu yok (ayrı `backup_perms` sidecar'ı
gerekmez); `db` container'a mount bile şart değil. TCP üzerinden bağlanır.

- **Tier'lar**: daily/weekly/monthly/yearly (Celery beat ile otomatik) + manual (sadece UI).
  Her tier'ın `enabled` + `retention` (GFS saklama adedi) ayarı `BackupPolicy`'de — dashboard
  Yönetici → **Yedekleme** sayfasından yönetilir. Beat task'ları her zaman tetiklenir ama yedek
  alıp almamaya `BackupPolicy.enabled` karar verir (tek doğruluk kaynağı).
- **Modeller** ([api/models.py](api/models.py)): `BackupPolicy`, `DatabaseBackup` (sürüm damgası:
  `app_version` + `migration_state`), `DatabaseRestore`. **Komutlar**:
  `backup_database --tier=daily [--force]`, `restore_database --backup-id=N --yes [--no-migrate]`.
  **Task'lar**: `api.tasks.backup_database_run` / `restore_database_run`. Yardımcılar
  [api/db_admin.py](api/db_admin.py) (`maintenance_connection`, `pg_env`, `compare_schema`).
- **Sürüm-bilinçli geri yükleme** (kritik — *"eski sürümün veritabanı problemli olabilir"*): her
  yedek alındığı APP_VERSION + migration durumuyla damgalanır. Geri yüklemede `compare_schema`
  çalışan kodun migration grafiğiyle karşılaştırır:
  - **exact** → RESTORE, migrate yok.
  - **forward** (yedek eski) → RESTORE → otomatik `migrate` (şemayı ileri taşı).
  - **block** (yedek koddan yeni) → reddedilir. **Filo ile bağ**: önce `.env` `IMAGE_TAG=<yedeğin
    sürümü>` → `up -d` (sahayı o sürüme indir), sonra geri yükle → exact match.
- **Geri yükleme yıkıcıdır**: `postgres` bakım DB'si üzerinden hedef DB'ye yeni bağlantı
  engellenir (`datallowconn=false`), açık oturumlar `pg_terminate_backend` ile düşürülür, DB
  drop + create edilir, `pg_restore` ile yedek yazılır. Kısa kesinti olur; sonrasında
  `celery_worker`/`celery_beat` restart önerilir. UI confirm modal'ı DB adını yazmayı ister.
- **Web'den**: yedek listesi + boyut + sürüm + uyumluluk rozeti; `.dump` indirme
  (`api_backup_download`, path-traversal korumalı, rol=1); manuel "Şimdi Yedekle"; geri yükleme
  geçmişi. Sayfa `api_backup_status`'ı 5 sn'de bir poll'lar, çalışan iş bitince yeniler.
- Beat task'ları [seed_periodic_tasks.py](scada_io/management/commands/seed_periodic_tasks.py)
  `BACKUP_TASKS`'ta — yeni sahada `seed_periodic_tasks` ile gelir. `.env`: `BACKUP_DIR`.

## MSSQL → PostgreSQL config göçü (tek seferlik)

Sistem MSSQL'den **PostgreSQL 16**'ya taşındı. Mevcut bir MSSQL sahasını taşırken **yalnız
konfigürasyon** taşınır (historian/Reading geçmişi taşınmaz, yeni DB'de sıfırdan birikir). İki
yönetim komutu (`api/management/commands/`): `export_config` (MSSQL'deyken `dumpdata`) +
`import_config` (yeni PG DB'de `loaddata` + **PostgreSQL sequence reset**). Taşınan/taşınmayan model
listesi [api/config_migration.py](api/config_migration.py) `CONFIG_MODELS`'te (sabit).

**Sıra zorunlu** (seed'lerden ÖNCE import; aksi halde `Station.get_or_create(id=1)` gibi sabit-PK
seed'lerle çakışır — `import_config` boşluk kontrolü bunu yakalar):
```
# Eski MSSQL sistem ayaktayken:
python manage.py export_config --output /tmp/config_export.json
# Yeni PostgreSQL boş DB:
python manage.py migrate --noinput
python manage.py import_config --file /tmp/config_export.json   # seed'lerden ÖNCE
python manage.py seed_initial_data && python manage.py seed_sais_data && python manage.py seed_periodic_tasks
```
`export_config` `use_natural_foreign_keys=True` kullanır: Django'nun yerleşik `auth.Permission` /
`ContentType` referansları doğal anahtarla yazılır (migrate yeniden ürettiğinde PK farkı sorun
olmaz); CONFIG modellerinin kendi FK'leri sayısal PK ile korunur. **Sequence reset atlanamaz** —
yoksa import çalışmış görünür, ilk yeni kayıtta duplicate-PK ile patlar (`import_config` otomatik yapar).

## Lisanslama (kurulum bazlı, imzalı, uzaktan)

Her kurulum **süreli lisansla** çalışır; lisans bitince **app hiçbir iş yapmaz** (polling + komut +
SIM/Envisoft yayını durur, dashboard tam kilitlenir). Generic kurulum altyapısı → `api/`. Filo
modeliyle aynı "pull": lisanslar merkezde (GitHub manifest), sahalar çeker.

- **İmzalı (Ed25519)**: lisanslar issuer'ın **private key**'iyle imzalanır; app gömülü
  **public key** (`settings.LICENSE_PUBLIC_KEY`) ile doğrular → müşteri yerel `License` kaydını/URL'i
  değiştirip süre uzatamaz. İmzalama aracı [scripts/license_tool.py](scripts/license_tool.py)
  (`keygen`/`issue`/`manifest`/`verify`) — **kullanıcının makinesinde**, app dışı. Private key
  ASLA repo'ya/app'e girmez (`.gitignore`).
- **Akış**: Celery beat `api.tasks.license_refresh_task` (6 saatte bir) + container startup
  (`refresh_license`) `LICENSE_URL` manifest'ini çeker → kendi `LICENSE_KEY` entry'sini bulur →
  imza doğrula → `License` singleton'a yazar. Enforcement **token tarihine** dayanır (ağsız);
  internet kesintisi süreyi bitirmez, uzatmak için manifest güncellenir.
- **Çekirdek** [api/licensing.py](api/licensing.py): `verify_token` / `apply_token` /
  `fetch_and_refresh` / `license_active()` (gate) / `license_status_dict()`. Model:
  `api.models.License` (singleton, `raw_token` cache + `signature_valid`). Komutlar:
  `refresh_license` (uzaktan), `apply_license --file=` (offline/elle token).
- **Enforcement (gate)**: `license_active()` çağrısı `scada_io.tasks.dispatch_polls` /
  `dispatch_commands` / `poll_connection` / `execute_command` ve `sais_domain.tasks.publish_minute_data`
  / `publish_cabinet_data` başında. **Çalışmaya devam eden** (kurtarılabilirlik): `license_refresh_task`
  + housekeeping (aggregate/prune/backup).
- **Dashboard tam kilit**: [dashboard/middleware.py](dashboard/middleware.py) `LicenseLockMiddleware`
  lisans aktif değilse `/dashboard/...` isteklerini `dashboard:license_expired` lock ekranına
  yönlendirir (muaf: login/logout/license-expired). `/admin/` (Jazzmin) superuser kurtarma için
  açık. Lock ekranı + admin **Lisans** sayfası (`admin_license`): "Şimdi Yenile" + elle token uygula.
  Aktifken bitişe `LICENSE_WARN_DAYS` (15) kala sarı banner (context processor `license_status` →
  base.html).
- **Enforcement env'den KAPATILAMAZ (güvenlik)**: `LICENSE_ENFORCE = (not DEBUG) or env_bool("LICENSE_ENFORCE", False)`
  ([settings.py](sais_web/settings.py)). Üretimde (`DEBUG=0`) `not DEBUG=True` → enforce **her zaman
  açık**; `.env`'de `LICENSE_ENFORCE=0` yazmak **etkisizdir** (eskiden tek-satırlık bypass'tı). Dev'de
  (`DEBUG=1`) varsayılan kapalı ama env ile **açılabilir** (enforce'u test etmek için; asla üretimde
  kapatamaz). Kalan tek vektör `DJANGO_DEBUG=1` (üretimde footgun) → kod obfuscation (PyArmor,
  [[project_code_obfuscation]]) ile mühürlenecek. **İç demo/"sınırsız" enforce kapatmak DEĞİL**, imzalı
  çok-uzun-süreli (perpetual) token'dır.
- **Deneme (trial) modu**: her iki installer da kurulumda **[1] Lisanslı / [2] Deneme (30 gün)** sorar.
  Deneme = lisans yok + `LICENSE_BOOTSTRAP_GRACE_HOURS=720` (30 gün) → `License.created_at`'ten itibaren
  30 gün çalışır, sonra kilitlenir. Lisanslı = anahtar/URL + grace 168 (token çekilene dek). Not: grace
  DB'deki `created_at`'e dayandığından DB-wipe trial'ı sıfırlar (tam sağlam trial = imzalı sabit-bitişli
  token; şu an obfuscation'a bırakıldı).
- **Sürüm çıkarma akışı (sen)**: `license_tool.py keygen` (bir kez, public key'i settings default'una
  koy) → `issue --key <saha> --customer <ad> --expires <tarih>` → `manifest *.json` → ayrı bir
  GitHub repo'ya push (imza sayesinde public olabilir). Saha `.env`: `LICENSE_KEY`, `LICENSE_URL`
  (enforce zaten üretimde açık — ayrıca yazmaya gerek yok). Uzatma = manifest'i güncelle; saha sonraki
  refresh'te alır (veya admin "Şimdi Yenile"). İç demo/perpetual = `issue --expires 2099-01-01`.

## Kod koruma / obfuscation (Cython) + sertleştirme

Yazılım kopyalanması/korsanlığına karşı katmanlı koruma. Kaynak Docker imajında; tek
çalışma-zamanı gate lisans olduğundan **kod açıktaysa gate patch'lenebilir** → obfuscation
şart. Lisans mantık açıkları (aşağıda) obfuscation'dan bağımsız olarak da kapatıldı çünkü
onlar **DB-veri** bypass'ıydı (kod gizlense de işlerdi).

- **Cython build-time obfuscation**: [build/cythonize_app.py](build/cythonize_app.py) seçili
  iş-mantığı modüllerini `.so`'ya derleyip `.py`/`.c`'yi siler. [Dockerfile](Dockerfile)
  `OBFUSCATE=1` (release.yml build-arg) RUN adımında `build-essential`+`cython`'ı **aynı katmanda**
  kurup kaldırır → nihai imajda derleyici YOK, seçili modüllerin `.py`'si YOK (yalnız `.so`).
  Yalnız `git tag` → release.yml çalışınca obfuscate edilir; dev `docker-compose.yml` düz `.py`.
  **DERLENMEZ** (script `NEVER` + glob): migrations, models.py, apps.py, `__init__`, management,
  templatetags, manage/wsgi/asgi/celery/settings/urls (Django introspection/entry-point/isim keşfi).
  İlk turda views/api_views/tasks/serializers/middleware/**forms** (metaclass) de hariç — başarılı
  Docker testinden sonra `COMPILE_GLOBS`'a eklenebilir. `--dry-run` ile hedef listesi test edilir.
  **Gerçek derleme testi Docker Linux gerektirir** (lokal Windows'ta gcc yok) → release build'de
  veya `docker build --build-arg OBFUSCATE=1 --build-arg PRODUCTION=1 ...` ile doğrulanır.
- **Enforcement env'den kapatılamaz**: `LICENSE_ENFORCE = PRODUCTION_BUILD or (not DEBUG) or env(...)`.
  `api/_buildflags.py` `PRODUCTION_BUILD` release build'de `True` yazılır + Cython'da mühürlenir →
  `DJANGO_DEBUG=1` ile bile enforce kapatılamaz.
- **valid_until DB bypass'ı kapandı**: `license_active` süreyi imzalı payload'dan okur (DB kolonu
  değil) — `UPDATE license SET valid_until=...` etkisiz. Bkz. `_revalidate_cached` (payload döndürür).
- **Grace reset kapandı**: bootstrap grace imaj `BUILD_EPOCH`'una çıpalı — `created_at` sıfırlansa da
  imaj grace'ten yaşlıysa kilit (`_within_bootstrap_grace`).
- **Node-lock sertleşti**: `runtime_fingerprint` parmak izini container'a salt-okunur mount'lu host
  `/etc/machine-id`'den hesaplar (`.env` düzenleyerek atlatma engellendi); mount yoksa env'e düşer.
- **Sır hijyeni**: `SaisCabinet.auth_secret` at-rest Fernet ([sais_domain/crypto.py](sais_domain/crypto.py)),
  hardcoded `envisoft21` kaldırıldı, `SECRET_KEY` üretimde fail-hard, `elazig-aat.json` git'ten çıkarıldı
  + `.dockerignore` private key'i hariç tutar. Testler: [api/tests.py](api/tests.py) (9 enforcement) +
  [sais_domain/tests.py](sais_domain/tests.py) (2 şifreleme).

## Web erişim / SSL (Caddy reverse proxy)

Dış erişim (`https://sais-tesis1.envisoft.com.tr` gibi) bir **Caddy** servisiyle sağlanır. Caddy
80/443 dinler, `web:8000`'e `reverse_proxy` yapar, otomatik HTTPS verir. Domain + SSL **dashboard'dan**
(Yönetici → **Web Erişim Ayarları**) yönetilir. Generic infra → `api/`.

- **Tek doğruluk kaynağı**: `api.models.WebSettings` singleton (pk=1). [api/web_proxy.py](api/web_proxy.py)
  `apply()` bu kayıttan paylaşılan `caddy_config` volume'ündeki **Caddyfile**'ı atomik üretir; Caddy
  `--watch` ile dosya değişince reload eder (outbound HTTP yok). `render_caddyfile` komutu container
  startup'ında (web command zinciri) ve her panel kaydında çağrılır.
- **3 TLS modu**: `letsencrypt` (otomatik ACME — domain + email + 443 erişimi gerekir), `manual` (PEM
  veya **PFX/.pfx** yükle → `web_proxy.pfx_to_pem` ile PEM'e çevrilir, `cryptography` pkcs12), `internal`
  (self-signed; enabled=False/domain yok → güvenli fallback).
- **ALLOWED_HOSTS/CSRF**: fleet-wide `.env`'de **wildcard** (`.envisoft.com.tr` /
  `https://*.envisoft.com.tr`) → panelden subdomain değişince **Django restart gerekmez**. Caddy tek-domain
  gatekeeper; prod'da web host'a publish edilmez (`expose: 8000`), ingress yalnızca Caddy.
  **TUZAK (saha deneyimi):** panelden (WebSettings) domain eklemek Caddy'yi yönlendirir ama
  **Django `ALLOWED_HOSTS`'u güncellemez** (o `.env`/settings'ten okunur). `.env`'de domain (veya
  wildcard) yoksa Django o Host'a **400 DisallowedHost** verir → kurulumda domain girilmeli (wildcard
  `.env`'e yazılsın); girilmediyse sonradan `DJANGO_ALLOWED_HOSTS` + `DJANGO_CSRF_TRUSTED_ORIGINS`'e
  elle eklenip web recreate edilmeli.
- **CDN/Cloudflare önde ise (saha deneyimi):** Cloudflare **"Flexible" SSL** modu origin'e HTTP ile
  gelir; Caddy otomatik HTTP→HTTPS yönlendirdiğinden **sonsuz redirect döngüsü** olur. Doğrusu:
  Cloudflare kaydını **"DNS only" (gri bulut)** yap (Caddy kendi LE cert'ini alır) **veya** turuncu
  bulut kalacaksa Cloudflare SSL modunu **"Full (strict)"** yap (origin'de geçerli LE cert doğrulanır).
  Turuncu bulutta bile Caddy HTTP-01 challenge'ı geçirilebildiği için cert alınabilir; asıl sorun SSL modu.
- **Compose**: `caddy` servisi (Watchtower label'sız → pinned, db/redis gibi) + `caddy_config_init`
  (busybox `chmod 0777`, web non-root yazsın) + `caddy_config`/`caddy_data` volume (cert kalıcılığı →
  Let's Encrypt rate-limit). Cert dosya izinleri: `key.pem` 0o600 / `cert.pem` 0o644.
- **Önkoşul (panel dışı)**: DNS A kaydı (public IP) + modem/firewall 80/443 yönlendirme. UI bunları
  uyarır. Windows volume'de `--watch` tetiklenmezse fallback `docker compose restart caddy`.
- **WSL2 port forwarding (kritik — dış erişim)**: Docker WSL2 içinde çalışır; WSL2 NAT modu publish
  edilen portları host'ta yalnız `127.0.0.1`'e yansıtır, LAN/public arayüze **değil** → dışarıdan
  80/443 timeout (modem doğru yönlendirse bile). [sais-stack.ps1](installer/scripts/sais-stack.ps1)
  her açılış döngüsünde `Set-PortProxy` ile `netsh interface portproxy` (host dış-arayüz IP →
  güncel WSL2 IP, 80+443) + Windows Firewall inbound kuralı kurar. WSL IP reboot'ta değiştiği için
  her `up -d` sonrası tazelenir. Bkz. memory `[[site-external-access-wsl-portproxy]]`.
- **Reverse proxy header'ları**: `_reverse_proxy_block` yalnız `X-Real-IP` yazar; Caddy
  `X-Forwarded-Proto/Host/For`'u zaten varsayılan geçirir (fazladan `header_up` "Unnecessary"
  uyarısı + log spam'i üretiyordu).
- `.env`: `CADDY_CONFIG_PATH`, `CADDY_CERT_DIR`, `CADDY_UPSTREAM`.

## Windows installer paketi (next-next-next)

Saha kurulumu tek `sais-setup-vX.Y.Z.exe` (Inno Setup) ile yapılır → [installer/](installer/). Sihirbaz
WSL2 + **Docker CE** kurar (Docker Desktop lisansı GEREKMEZ), GHCR'dan image çeker, compose yığınını
başlatır, ilk veriyi tohumlar, açılışta otomatik kalkan **NSSM Windows servisi** kaydeder.

- **Akış**: [sais_setup.iss](installer/sais_setup.iss) sihirbazı (lisans/DB/domain-TLS/admin) →
  answers JSON → [install.ps1](installer/scripts/install.ps1) orkestratör → `00-ensure-docker`
  (WSL2+Docker CE; reboot gerekirse RunOnce ile devam) → `10-configure` (env.template → `.env`, secret +
  PostgreSQL şifresi üret, domain'den wildcard türet) → `20-up` (gömülü read-only GHCR token ile login → pull
  → up -d) → `30-firstrun` (seed_initial/sais/admin + WebSettings bootstrap, marker ile idempotent) →
  `40-register-service` (NSSM `SAISScada` → `sais-stack.ps1`).
- **Docker erişimi**: tüm `docker compose` çağrıları WSL2 içinde çalışır
  ([_common.ps1](installer/scripts/_common.ps1) sarmalayıcıları); compose + `.env` Windows'ta `C:\SAIS`,
  WSL `/mnt/c/SAIS`'ten erişir.
- **WSL kullanıcısı root'a SABİTLENDİ (`$script:WslUser`) — TUZAK (saha deneyimi):** Docker'a dokunan
  her `wsl.exe` çağrısı **açıkça `-u root`** ile koşar; distro'nun *varsayılan* kullanıcısına asla
  güvenilmez. Sebep: `00-ensure-docker` distro'yu `wsl --install -d Ubuntu **--no-launch**` ile kurar →
  Ubuntu OOBE hiç çalışmaz → Unix kullanıcısı oluşmaz → varsayılan **root**'tur ve docker çalışır. Ama
  sahada **biri bir kez interaktif `wsl` yazarsa** OOBE tetiklenir, Windows hesabından türetilmiş bir
  kullanıcı (ör. `envisoftwebx`) oluşturur ve onu **varsayılan** yapar; bu kullanıcı `docker` grubunda
  olmadığı için tüm `docker compose` çağrıları `permission denied ... /var/run/docker.sock` verir.
  **Arıza sinsidir**: container'lar `restart: unless-stopped` sayesinde ayakta kalır (site çalışıyor
  görünür) ama [sais-stack.ps1](installer/scripts/sais-stack.ps1) döngüsünde `up -d` fırlatınca
  **`Set-PortProxy` (dış 80/443 köprüsü) ve `sleep infinity` (WSL2 VM keep-alive) adımlarına hiç
  ulaşılmaz** → sonraki WSL IP değişiminde dış erişim sessizce ölür. **Ayrıca:** OOBE `/etc/wsl.conf`'a
  `[user] default=...` yazar ve bu **registry `DefaultUid`'i EZER** → `wsl --manage --set-default-user`
  veya `DefaultUid=0` "başarılı" der ama etkisiz kalır; geri almak için `/etc/wsl.conf` düzeltilip
  `wsl --terminate` gerekir. `-u root` sabitlemesi bu sınıfı tamamen kapatır.
- **Admin (rol=1)**: [users/seed_admin_user](users/management/commands/seed_admin_user.py) non-interactive
  (env `DJANGO_SUPERUSER_*`) — `createsuperuser --noinput` CustomUser `rol` alanını set edemediği için.
- **DB**: **PostgreSQL 16** (açık kaynak, lisans gerektirmez; bundled `postgres:16` container). Şifre installer'da otomatik üretilir (`POSTGRES_PASSWORD`).
- **GHCR image private** → installer'a `read:packages` scope'lu token build-time gömülür (release.yml
  `windows-installer` job, repo secret `INSTALLER_GHCR_TOKEN`). Asıl kullanım gate'i **Ed25519 lisans**,
  image gizliliği değil. `nssm.exe` build'de [nssm.cc](https://nssm.cc)'den çekilir (repo'ya commitlenmez).
- **Test**: dev ortamında doğrulanamaz; temiz Windows VM'de manuel (bkz. [installer/README.md](installer/README.md)).

## Linux tek-komut kurulum (Windows installer'ın karşılığı)

Windows dışı sahalar (bulut/kiralık Ubuntu/Debian sunucular, ör. Server 2019 gibi WSL2'siz
Windows'a alternatif) için [installer/install-linux.sh](installer/install-linux.sh) — **self-contained
tek script**. Windows installer'ının yaptığı işin aynısını yapar; Windows'takinden farkı: WSL2 katmanı
yok (Linux'ta Docker native çalışır), servis NSSM/scheduled-task yerine **systemd**.

- **Kullanım**: dosyayı sunucuya kopyala → `sudo bash install-linux.sh` (interaktif, soru sorar) veya
  unattended env ile (`DOMAIN`/`ADMIN_USER`/`ADMIN_PASS`...). Repoya ihtiyaç yok —
  `docker-compose.prod.yml`'yi kendisi (embedded heredoc) yazar.
- **GHCR token gömülü (müşteri token GİRMEZ)**: Windows installer'daki gibi read:packages token'ı
  **build-time** script'e gömülür — [release.yml](.github/workflows/release.yml) `linux-installer` job'ı
  `__GHCR_USER__`/`__GHCR_TOKEN__`/`__GHCR_IMAGE__` placeholder'larını `INSTALLER_GHCR_TOKEN` secret'ıyla
  `sed`'ler ve script'i release asset'i olarak ekler. Script placeholder'ları değişmemişse (repo/dev)
  boş sayar → token yalnız geliştiriciye sorulur; müşteri sürümünde gömülü olduğundan hiç sorulmaz.
- **Akış (6 adım)**: Docker CE kur (`get.docker.com`) → `.env` üret (`DJANGO_SECRET_KEY` +
  `POSTGRES_PASSWORD` + `WATCHTOWER_API_TOKEN` rastgele; domain'den wildcard ALLOWED_HOSTS/CSRF türet) →
  GHCR login (`--password-stdin`) → `pull` + `up -d` → migration bekle + `seed_initial_data`/
  `seed_sais_data`/`seed_admin_user` (retry'li) → systemd unit `envisoft-webx.service`
  (`enable`, açılışta `compose up -d`).
- **Makine parmak izi (node-lock)**: Windows'ta `sais-stack.ps1` MachineGuid+baseboard'dan üretip
  `.env`'e yazar; Linux karşılığı `install-linux.sh` içinde `sha256(/etc/machine-id [+ DMI product_uuid])`
  → `MACHINE_FINGERPRINT`. `machine-id` kalıcı olduğundan kurulumda bir kez hesaplanır (Windows'taki
  her-boot tazelemesine gerek yok). Boşsa node-lock uygulanmaz (lisans yalnız süreye bakar).
- **Domain/SSL**: script yalnız ALLOWED_HOSTS/CSRF wildcard'ını `.env`'e yazar; Caddy'ye domain
  **yazmaz** (WebSettings bootstrap yok) — SSL/domain sonradan dashboard → Web Erişim Ayarları'ndan
  açılır. Kurulum dizini varsayılan `/opt/envisoft`.
- **Lisans**: kurulumda **[1] Lisanslı / [2] Deneme (30 gün)** sorulur (bkz. Lisanslama bölümü). Deneme
  → grace 720s; Lisanslı → anahtar/URL + grace 168. Enforce env'e yazılmaz (üretimde kod ile açık).
- **DRY notu**: embedded compose, [docker-compose.prod.yml](docker-compose.prod.yml) ile **elle senkron
  tutulmalı** (prod compose değişince bu heredoc da güncellenmeli). Satır sonları **LF** olmalı (CRLF →
  Linux'ta `bash` patlar).

## Kurulum kılavuzu (docs/ — otomatik üretilen)

Windows + Linux kurulumu, kurulum öncesi kontroller (sanallaştırma/nested, Windows sürümü, port/DNS)
ve karşılaşılabilecek hatalar tek bir kullanıcı kılavuzunda toplanır (kurulum ekibi/müşteri için).

- **Tek doğruluk kaynağı**: [docs/generate_install_guide.py](docs/generate_install_guide.py) (python-docx).
  Metin bu script'te; çalıştırınca `docs/Envisoft-WebX-Kurulum-Kilavuzu.docx` üretir. PDF docx'ten
  türetilir (yerelde Word COM veya `docx2pdf`; CI'da LibreOffice `soffice --convert-to pdf`). Üretilen
  `docs/*.docx` + `docs/*.pdf` repo'ya commit'lenir (`.gitattributes`'ta `binary`).
- **Otomatik güncelleme**: [.github/workflows/install-guide.yml](.github/workflows/install-guide.yml)
  `installer/**` veya üretici script değişince docx+pdf'i yeniden üretip repo'ya geri commit'ler.
  Döngü yok (bot yalnız `docs/*.docx|pdf` yazar, bunlar tetikleyici path'lerde değil).
- **Kural**: kurulum davranışı değişince (installer script, ön koşul, yeni hata/çözüm) kılavuz
  metnini `generate_install_guide.py` içinde güncelle; docx/pdf CI'da (veya elle
  `python docs/generate_install_guide.py` + PDF dönüştürme) yenilenir.

## Önemli çalıştırma davranışları

- **Yapılandırma:** Tüm ayarlar `.env` üzerinden okunur (`python-dotenv`). Sırları asla koda commitlemeyin. Ek env'ler: `API_LOG_*`, `READING_RETENTION_*_DAYS`, `CELERY_BROKER_URL` (default `redis://localhost:6379/2`), `CELERY_RESULT_BACKEND` (default `django-db`).
- **`USE_TZ=True` zorunlu** — django-celery-beat 2.9 + Celery 5.6 kombinasyonu USE_TZ=False'ta sessizce task tetiklemiyor (`is_due()` timezone karşılaştırmasında hata). Default `.env` `DJANGO_USE_TZ=1`; değiştirme.
- **DEBUG=False** modunda HSTS, güvenli çerez ve Whitenoise manifest storage aktiftir.
- **Seed komutları idempotenttir** — `seed_initial_data`, `seed_sais_data`, `seed_periodic_tasks` birden çok kez çalıştırmak güvenlidir (`get_or_create` / `update_or_create`).
- **pymodbus 3.11+ API:** readers/writers `device_id=` parametresi kullanır (pymodbus 3.13'te `slave=` → `device_id=` rename edildi). Bu dokümantasyonda veya PR'da `slave=` görürsen o eski API'dir.
- **Modbus timeout + retry (Connection-level, pymodbus'a bağlı):** Tüm Modbus reader/writer'ları pymodbus client'ını `timeout=Connection.timeout_ms/1000` + `retries=Connection.retry_count` ile kurar. pymodbus cevapsız (TimeoutError) okumada `retries` kadar ek dener → **toplam deneme = 1 + retry**, her deneme timeout kadar bekler (ör. retry=3, timeout=2000 → cevapsız okuma ~8 sn bloklar; Modbus exception yanıtı/illegal-address ANINDA döner, retry edilmez). `retry_count` eskiden ölüydü (pymodbus örtük default 3 kullanıyordu); artık canlı — migration `0031` mevcut inert default 1'i 3'e taşıyıp çalışan davranışı korudu, model default 3. **Cevap vermeyen/yavaş noktalarda `retry_count`'u düşürmek (0–1) poll cycle'ı hızlandırır** ve timeout-bloklama kaynaklı overlap/bayatlığı azaltır (İletişim Tanılama sayfasından izle).
- **SCADA polling akışı:** `scada_io.tasks.dispatch_polls` her 5 sn'de çalışır; `Connection.last_polled_at + poll_interval_sec` due olan bağlantılar için `poll_connection.delay(conn_id)` enqueue eder. Worker pool'dan cached reader alır (veya yeni bağlantı açar):
  1. Connection'ın aktif `ScanGroup`'larını **tek Modbus request** ile okur (`reader.read_raw`), ham register listelerini bellekte tutar.
  2. Her sensör için: `scan_group` varsa batch cache'inden `decode_sensor_from_batch` ile değer çıkar; yoksa legacy `reader.read(sensor)` ile tek-tek okur.
  3. `persist_reading` ile Reading insert + SensorLatest upsert.
  Reader factory `Connection.protocol` bazlı: `modbus_tcp` (MBAP), `modbus_rtu_over_tcp` / `modbus_ascii_over_tcp` (gateway transparent mode — RTU/ASCII frame TCP socket üzerinden), `modbus_rtu` / `modbus_ascii` (serial port), `ascii_custom` (pyserial request-response).
- **Persistent connection pool** (`scada_io.connection_pool`): Worker process'e özel, module-level socket cache. Her polling'de open/close yerine aynı socket polling cycle'ları boyunca açık kalır. RUT906/Moxa gibi gateway'lerde session pool kilitlenmesini önler. Connection-level hata ("bağlantı yok/broken/reset/socket/disconnected") tespit edilince `invalidate()` — sonraki cycle'da taze socket. 3 peş peşe başarısız cycle'da watchdog invalidate. TCP keep-alive (Linux: 60s/10s/3 probe; Windows: SO_KEEPALIVE + OS default).
- **Ölü soket (broken pipe) savunması — idle-aware pool + cycle içi retry:** Pool **worker process'e özel** olduğundan prefork'ta (`--concurrency=4`) aynı PLC'ye **worker sayısı kadar socket** açılır; her socket cycle'ların ancak 1/N'inde kullanılır → aralarda **boşta kalır** → PLC boştaki Modbus TCP oturumunu kapatır → biz ölü sokete yazınca **`BrokenPipeError [Errno 32]`** alırız ve o cycle'ın **TÜM** sensörleri bad olur (SIM'e bozuk dakika gider). Saha kanıtı (Mikrodev PLC): tek anda **3 açık socket** + periyodik broken pipe, tüm sensörler aynı saniyede, %2 hata. İki savunma: **(1)** `get_reader` bayat socket'i (son kullanım > `POOL_MAX_IDLE_SEC`, default 60sn; 0=kapalı) kullanmadan önce kapatıp yeniden açar; **(2)** `poll_connection` hata kategorisi `conn_reset` ise (ölü socket) pool'u invalidate edip cycle'ı **bir kez** tekrarlar → veri kaybı olmaz. **Timeout'ta retry YAPILMAZ** (taze socket cevapsız cihazı konuşturmaz, yalnız bloklamayı ikiye katlar) — ayrım `classify_comm_error` ile yapılır. Okuma/persist ayrımı için bkz. `scada_io.tasks._read_cycle`. **Kalıcı çözüm** (henüz yapılmadı): polling'i ayrı kuyruk + `--concurrency=1` worker'a alıp PLC başına **tek sıcak socket** bırakmak (compose değişikliği gerektirir).
- **ScanGroup batch reads (Geo SCADA scanner pattern):** Bir `Connection` üzerinde çoklu `ScanGroup` (slave_id + function + start_address + quantity) tanımlanır. Her grup tek Modbus request'te okunur; grup'a bağlı tüm `Sensor`'lar o batch'ten offset ile decode edilir. **Quantity hard-enforce**: function 3/4 için max 125, function 1/2 için max 2000. `Sensor.address` scan_group aralığında olmalı (clean() validation). `scan_group=None` sensörler legacy per-sensor read mod'unda kalır (geri uyumlu).
- **Sensör decode:** `scada_io.decoders.decode_registers` 12 data_type (int16/uint16/int32/uint32/int64/uint64/float32/float64/bool/bit/string/raw) × byte_order × word_order kombinasyonlarını destekler. `Sensor.scale * raw + Sensor.offset` mühendislik dönüşümü uygulanır; `Sensor.decimals` verilmişse `round(value, decimals)` ile float yuvarlaması. Bit-okuma: register içindeki `bit_position`. ASCII: `ascii_request` gönder + `ascii_response_regex` ile değer çıkar.
- **Sensör simülasyonu:** `Sensor.is_simulated=True` ise reader cihaza dokunmaz; `scada_io.tasks._record_simulated` `parameter.min_range`/`max_range` arası rastgele değer üretir (`Reading.origin='simulated'`).
- **Reading vs SensorLatest + save_interval_sec:** `SensorLatest` per-sensor snapshot (HMI anlık); `Reading` time-series historian. `persist_reading` snapshot'ı **her polling'de** günceller, ama `Reading` insert'i `Connection.save_interval_sec` ile gated: null ise her polling'de; değer verilmişse sensör başına o sürede bir insert (DB şişmesini önler). Tipik: poll_interval=10sn, save_interval=60sn → HMI 10sn'de taze, historian dakikada 1 satır. `SensorLatest.last_change_at` değer değiştiğinde, `last_saved_at` Reading insert'inde, `update_count` her snapshot'ta güncellenir.
- **Dijital "Son Değişim" (aktif/pasif geçişi, comm-error hariç):** `SensorLatest.last_digital_change_at` yalnız dijital sensörlerde (sensor_type 2/3) **aktif/pasif** durumu değiştiğinde damgalanır; `last_digital_state` son GEÇERLİ ham durumu tutar (karşılaştırma referansı). `persist_reading` bu bloğu yalnız `value is not None` (yani iletişim/decode hatası değil) iken çalıştırır → comm-error'da `value=None` gelir, blok atlanır, önceki durum korunur; comm-recovery aynı duruma dönerse SAHTE değişim sayılmaz. `last_change_at`'in aksine bu alan comm-error'la kirlenmez. Anasayfa Dijital Kanallar widget'ında "Son Okuma" yerine bu **"Son Değişim"** gösterilir (`home_snapshot` → `digital_change` + humanize `digital_change_ago`; bayatlık uyarısı yine `readtime`'a dayanır).
- **Değişimde kaydet (COV / deadband):** `Sensor.save_on_change=True` ise `Reading` insert'i değişim-bazlı olur (report-by-exception): değer son **kaydedilen** değerden `Sensor.deadband`'i aşacak kadar farklı (analog; null/0 = her fark), status değişmiş, veya `Sensor.cov_heartbeat_sec` dolmuşsa yazılır. Aksi halde sadece snapshot güncellenir (HMI taze kalır). `save_interval_sec` yine minimum aralık (throttle) olarak uygulanır — heartbeat hariç. Karşılaştırma referansı `SensorLatest.last_saved_value` / `last_saved_status` (anlık snapshot değil son **kayıt** — yoksa deadband altında kalan yavaş drift hiç tetiklenmez). Deadband hesabı için bkz. `scada_io.persistence._cov_changed`. `save_on_change=False` (default) ise davranış değişmez: throttle dışında her okumada yazılır.
- **Aggregate tabloları:** `ReadingFiveMin` / `ReadingFifteenMin` / `ReadingHourly` / `ReadingDaily` — sensör başına bucket avg/min/max/count/bad_count (hepsi `ReadingAggregateBase` alt sınıfı). Dashboard'lar bunları sorgulasın, raw `Reading`'i taramasın. `aggregate_readings` komutu (`--bucket=5m|15m|hour|day`) veya `api.tasks.aggregate_readings_*` Celery task'larıyla doldurulur. **5m** bucket numune senaryosu motorunun poll-sıklığından bağımsız kısa-pencere ortalaması için her dakika hesaplanır.
- **Numune alma senaryosu (no-code kurucu + motor, SAIS-özel → `sais_domain/`):** Eski sabit-kodlu `controlSampleValues` akışı kullanıcı-tanımlı jenerik bir yorumlayıcıya taşındı. Modeller: `Scenario` (kütüphane; `kind=auto|ministry`, `is_active`/`is_builtin`, `avg_window=5m|15m`, `trigger_mode=any|all|n_of_m`), `ScenarioParameter` (izlenen parametre + min/max eşik), `ScenarioStep` (sıralı; `after_seconds` tetikten ofset + `require_still_exceeded` + `actions` JSON listesi: notify/sampler_on/sampler_off/ministry_get_code/sim_sample_start|complete|error/send_diagnostic), `ScenarioRun` (yürütme örneği; durum + `last_step_order` cursor + `sample_code`), `ScenarioRunLog` (eval/step/skip denetim). Motor [sais_domain/scenario_engine.py](sais_domain/scenario_engine.py) `run()` her dakika (`sais_domain.tasks.run_scenarios`, lisans-gate'li): aktif `auto` senaryoyu aggregate ortalamasına karşı değerlendirir (skip: yıkama aktif veya `SensorLatest.status ∈ {8,23,24,25,26}`), tetik kombinasyonu sağlanınca `ScenarioRun` açar; açık run'ları **monoton** `after_seconds <= elapsed` ile ilerletir (kaçan tick adım atlamaz, gün-devri güvenli). Aksiyonlar: `Command` (idempotency `scenario:{run}:{order}:on|off`, `source='rule'`) + `SaisSimClient` (SampleRequest*/SendDiagnosticWithType 701/702) + `send_bulk` bildirim. İki yerleşik şablon (`seed_sais_data`): "Varsayılan Otomatik Senaryo" + "Bakanlık Talepli Senaryo" (silinemez). UI: operatör [scenario_builder.html](dashboard/templates/dashboard/operator/scenario_builder.html) 4 sekme (Senaryolar/Kurucu/Durum/Geçmiş) + `dashboard/api_views.py` `scenario_*` AJAX endpoint'leri. Debug: `python manage.py run_scenarios_once`.
- **Sistem alarmları (Sistem Kontrol → Sistem Alarmları sekmesi):** Beş kategori sistem-seviyesi uyarı, her biri toggle + kendi ayarıyla; bildirim `api.notifications.send_bulk(... kind="alarm")` ile **operatöre SMS/e-posta** (bildirim tercihi açık aktif kullanıcılar, Bakanlık rol=4 hariç). Ayar singleton `SystemAlarmSettings`, throttle state `SystemAlarmState` (`(alarm_type, ref_key)` → son bildirim; cooldown ile tekrar bildirim sınırlanır, kullanıcı boğulmaz). Motor [sais_domain/system_alarms.py](sais_domain/system_alarms.py). İki beat: `check_system_alarms` (10 dk) + `check_system_alarms_daily` (günlük). **(1) Bakanlık veri hatası** — son 10 dk `GetDataByBetweenTwoDate`; seçili hata status'ları (varsayılan 200–206 eksik/geçersiz yıkama/kalibrasyon/akış/debi/tekrar/birim; tüm `_Status` + `_N_Status` taranır) `persist` dk'dan uzun tekrarlıysa bildir — tek-sefer anlık hatalar atlanır; lisans + `data_error_enabled` gate'li (varsayılan kapalı, ministry sorgusu yaptığı için opt-in). **(2) SSL** — `WebSettings` domain'ine canlı TLS (Let's Encrypt) veya `manual_cert_not_after`; bitişe `ssl_warn_days` (3) kala. **(3) Kalibrasyon** — kabinli aktif istasyonların son `Calibration.time_iso`'su; `interval − warn` (29) gün geçince. **(4) Lisans** — `license_status_dict().days_remaining`; bitişe `license_warn_days` (7) kala. **(5) PowerOff** — `poweroff_min_minutes`'tan uzun her kesinti **bir kez** (enerji/PC/bağlantı). Debug: `python manage.py check_system_alarms_once [--realtime|--daily]`. Tüm Bakanlık status kodları (27) `StatusCode`'a seed migration `0027` + `seed_initial_data` ile yüklenir.
- **Eksik veri yeniden gönderimi (SAIS-özel → `sais_domain/`):** `sais_domain.tasks.resend_missing_data` her **6 saatte** bir (beat) aktif kabinler için Bakanlık `GetMissingDates` (son 48 saat eksik dakika listesi) servisini sorgular, dönen dakikaları **`Reading` historian'ından** doldurup `SendData` ile yeniden iletir. Backfill payload'ı [services.py](sais_domain/services.py) `build_sim_payloads_for_times` ile üretilir (`build_sim_payload`'ın tarihsel karşılığı): yalnız analog sensörler, her dakika için o dakika sonuna kadarki **son** okuma (held value); o dakikada hiç aktivite yoksa (PC kapalı / polling durmuş) dakika atlanır — uydurma `-9999` gönderilmez. `SimStatusPolicy` filtresi uygulanır; yıkama `force_status` override'ı tarihsel veriye uygulanmaz. Gate: **lisans aktif** + **`SystemSwitch.sim_enabled`** (SIM iletimi kapatılınca eksik veri servisi de durur). Tek run'da `SAIS_MISSING_RESEND_MAX_MINUTES` (default 720) dakika sınırı; kalan sonraki run'da toparlanır. Durum (`last_missing_check_at` / `_found_count` / `_resent_count` / `_error`) `SystemSwitch`'e damgalanır → Sistem Kontrol sayfasında görüntülenir (`system_control_status` 10 sn poll). Debug: `python manage.py resend_missing_data_once`.
- **Komut tetikleme:** `StartSampleView` → `Command` tablosuna `idempotency_key='ministry_sample:{station}:{code}'` ile kayıt; `priority=10`, `expires_at=+5dk`. `scada_io.tasks.dispatch_commands` her 3 sn'de pending komutları atomik `pending → queued` yapıp `execute_command.delay(cmd_id)` enqueue eder. Worker writer açar, yazar, status `executing → completed/failed`. Başarısızsa `attempt_count < max_attempts` iken `pending`'e geri döner (otomatik retry); `expire_commands` her 60 sn'de süresi dolanları `expired` yapar.
- **İletişim hata teşhisi (comm diagnostics — "bizden mi PLC/ağdan mı?"):** Sensör okumadaki iletişim hatalarının kaynağını teşhis eder (jenerik SCADA → çekirdek). Kilit: pymodbus/reader hata metni kök-nedeni taşır ama şu ana dek yalnız `Connection.last_error_message`'a *son hali* yazılıp kayboluyordu. [scada_io/comm_errors.py](scada_io/comm_errors.py) `classify_comm_error(text)` hata metnini kategorilere indirir (`timeout`/`conn_reset`/`conn_open`/`illegal_address`/`illegal_function`/`illegal_value`/`slave_failure`/`decode`/`crc_frame`/`unknown`) + `category_source` (config=biz / device / network / line / ambiguous). **Modbus `ExceptionResponse` (illegal address/function/value) `exception_code=`'dan okunur** → cihaz-reddi = neredeyse her zaman bizim config. Yakalama: `poll_connection` hata noktalarında `_record_comm_error` ile **`CommErrorEvent`** ([api/models.py](api/models.py)) yazar (`connection`+opsiyonel `sensor`+`category`+`source`+`status_code`+`detail`+`time_iso`) — **salt gözlem**, `status_code`/SIM/polling davranışını DEĞİŞTİRMEZ; worker-process cooldown (`COMM_ERROR_MIN_INTERVAL_SEC=300`) ile hacim sınırlı; retention `prune_comm_errors` (`COMM_ERROR_RETENTION_DAYS=30`, gecelik `api.tasks.prune_comm_errors_task`). **En keskin us-vs-PLC sinyali:** bir bağlantıda tüm sensörler birlikte mi bad (→ PLC/ağ) yoksa belirli sensörler mi hep bad, diğerleri sağlıklı (→ bizim adres/slave config). Hata oranı yalnız gerçek hata kodlarından (`{0,4,8}`+status-yok) hesaplanır — operasyonel kodlar (yıkama/bakım 23-26, aralık-dışı 39) hariç. **Karar tek yerde:** `comm_errors.verdict(err_rate, sensor_bad_rates, category_counts)` → `(metin, ok|warn|err)`; komut ve dashboard aynı fonksiyonu kullanır. İki sinyal: **(1)** kategori kaynağı baskınlığı (`network` ≥%70 → bağlantı seviyesi; `config` ≥%70 → sensör seviyesi), **(2)** **eş-zamanlılık proxy'si** — sensörlerin hata oranları birbirine yakınsa (`spread ≤ %10`) hepsi BİRLİKTE düşüyor → bağlantı seviyesi. (2) kritik: epizodik soket kopmasında oran düşüktür (%2) ve hiçbir sensör "sürekli hatalı" eşiğini geçmez; yalnız per-sensör orana bakan kural bunu göremez. **Anında analiz** (şema değişikliği olmadan mevcut DB'de): `python manage.py diagnose_comm_errors [--station N] [--connection N] [--days 7] [--csv path]`. **Dashboard:** Sensör Ayarları → **İletişim Tanılama** (`/dashboard/sensor-config/comm-diagnostics/`, operatör+admin) — bağlantı sağlığı + kategori dağılımı grafiği + saatlik trend + son ham hatalar, canlı poll (15 sn); veri [dashboard/api_views.py](dashboard/api_views.py) `comm_diagnostics_data`.
- **API request logging:** `api.middleware.ApiLoggingMiddleware` her gelen isteği `ApiLog(direction='in')` olarak kaydeder; süre ölçer, `request.user`/IP/user-agent yakalar, hassas header (`Authorization`/`Cookie`/`X-API-Key`) ve body key'leri (`password`/`secret`/`token`/`api_key`) maskeli. Skip path'ler: `/static/`, `/media/`, `/__debug__/`, `/admin/jsi18n/`, `/favicon.ico`. Giden HTTP çağrıları için `api.api_logging.log_outbound_call` decorator veya `record_outbound_call(...)` helper kullanılır.
- **Retention:**
  - `prune_api_logs` (`API_LOG_RETENTION_DAYS`, default 90gün) — günlük cron.
  - `prune_readings` 4 seviye (`READING_RETENTION_RAW_DAYS=90`, `..._15M_DAYS=365`, `..._HOURLY_DAYS=1825`, `..._DAILY_DAYS=99999`) — günlük cron. Batch delete (10K/transaction) ile tek büyük transaction / uzun kilitten kaçınır.
- **PC kapanma kaydı (PowerOff, heartbeat bazlı):** Çalışan stack `api.tasks.heartbeat_task` ile her dakika `SystemHeartbeat` (singleton pk=1) `last_seen` damgasını tazeler. Container açılış zincirinde `detect_power_off` (migrate sonrası) son damga ile şimdiki zaman arasındaki boşluğu ölçer; `POWEROFF_DETECT_THRESHOLD_MIN` (default 5dk) aşılırsa o aralığı **her istasyon için** bir `PowerOff` kaydına yazar (`start_date`=son damga ≈ kapanma anı, `end_date`=açılış). Elektrik kesintisi/sert kapanmayı da yakalar (graceful sinyale bağlı değil); kısa container restart'ları (Watchtower) eşik altında kalıp kayıt üretmez. Heartbeat task lisans-gate'siz (PC ayaktayken damga durmamalı). Eski SIGTERM bazlı `api/signals.py` kaldırıldı.
- **Dashboard arayüz** (`/dashboard/`): Metronic 8.2 tabanlı light-sidebar layout. Giriş `/dashboard/login/` (Metronic corporate template, sosyal login/signup yok); "şifremi unuttum" admin'e yönlendiren info sayfası. Ana sayfa 4 widget'lı (KPI/grid/trend/events) AJAX polling ile canlı. Role mapping: rol=1 (admin) her menüyü görür, rol=2 (operatör) admin dışı, rol=3 (user) salt-izleme (komut/sistem log yok). Session cookie 24h (`SESSION_COOKIE_AGE=86400`); "Beni hatırla" işaretlenirse 30 gün.
- **Dil desteği (i18n):** `USE_I18N=True`, `LANGUAGES=[("tr","Türkçe"),("en","English")]`, `LOCALE_PATHS=[dashboard/locale]`. TR default, EN çevirisi `dashboard/locale/en/LC_MESSAGES/django.po` (178 entry). `django.mo` dosyası commit'te; gettext binary olmadan Python script ile compile edildi. Yeni string eklendiğinde ya Linux/Docker'da `python manage.py compilemessages` ya da `scripts/compile_po.py <po yolu>` (pure-Python msgfmt) kullanılmalı (Windows gettext eksik).
- **Bağlantı ağacı dışa/içe aktarım (klonlama):** Sensör Ayarları → Bağlantılar sayfasından bir bağlantının tüm alt ağacı (Connection + ScanGroup'lar + Sensör'ler + sensörlerin ihtiyaç duyduğu Parameter tanımları) tek tuşla taşınabilir JSON'a **dışa aktarılır** ve başka bir tesise **içe aktarılır** — birebir aynı kurulan sahalarda scan/sensör ağacını klonlamak için. Jenerik SCADA → çekirdek [api/connection_io.py](api/connection_io.py) (`export_connection` / `import_connection`, format `sais_web.connection_export` v1). Kurallar: runtime/id/FK alanları taşınmaz; sensör→scan_group referansı **grup adı**, sensör→parameter referansı sentetik anahtar (`p0/p1...`) ile tutulur; import hedef tesiste parametreleri `parameter_name` (kod) ile **find-or-create** eder, bağlantı adı çakışırsa otomatik son ek (` (2)`) ekler, sensör tag'lerini boşaltıp yeniden ürettirir (benzersizlik). EnvisoftChannel/Bakanlık eşlemeleri kapsam dışı (tesis-özel). UI: dışa aktar = `ConnectionExportView` (GET, JSON download), içe aktar = `connection_import` (POST multipart) + `connection_list.html` toolbar modalı. Yetki: operatör (rol 1/2). Tümü tek transaction.

## Periyodik task'lar (Celery beat)

Tüm periyodik iş Celery beat'in DB'de tuttuğu `PeriodicTask` kayıtlarıyla yönetilir — `seed_periodic_tasks` komutu idempotent olarak yazar:

| Task | Periyot | Amaç |
|---|---|---|
| `scada_io.tasks.dispatch_polls` | her 5 sn | Due olan Connection'lara `poll_connection` enqueue |
| `scada_io.tasks.dispatch_commands` | her 3 sn | Pending Command'lara `execute_command` enqueue |
| `scada_io.tasks.expire_commands` | her 60 sn | `expires_at` geçmiş komutları `expired` yap |
| `api.tasks.aggregate_readings_5m` | `* * * * *` | Son 2 saatlik 5dk bucket aggregate (numune senaryosu için) |
| `api.tasks.aggregate_readings_15m` | `*/5 * * * *` | Son 2 saatlik 15dk bucket aggregate |
| `api.tasks.aggregate_readings_hourly` | `5 * * * *` | Son 6 saatlik saatlik aggregate |
| `api.tasks.aggregate_readings_daily` | `0 1 * * *` | Son 48 saatlik günlük aggregate |
| `api.tasks.prune_readings_task` | `30 2 * * *` | Reading + aggregate retention'ı geçenleri sil |
| `api.tasks.prune_api_logs_task` | `0 3 * * *` | `API_LOG_RETENTION_DAYS`'den eski kayıtları sil |
| `sais_domain.tasks.publish_minute_data` | `* * * * *` | Aktif kabinler için SIM + Envisoft `SendData` fan-out |
| `sais_domain.tasks.run_scenarios` | `* * * * *` | Aktif numune senaryolarını değerlendir + açık run'ları ilerlet |
| `sais_domain.tasks.resend_missing_data` | `0 */6 * * *` | Bakanlık `GetMissingDates` → eksik dakikaları `Reading`'den backfill → `SendData` (lisans + `sim_enabled` gate'li) |
| `sais_domain.tasks.compute_sim_valid_stats` | `30 1 * * *` | Geçerli veri istatistiği: ayın günlerini **gün gün** (kısım kısım) `GetDataByBetweenTwoDate` ile çekip `SimValidDay`'e damgalar (Dinamik Veri Raporu aylık geçerli kartı bunu DB'den okur — canlı ay-sorgusu yok) |
| `sais_domain.tasks.check_system_alarms` | `*/10 * * * *` | Sistem alarmları (gerçek-zamanlı): son 10 dk Bakanlık verisi → seçili hata kodları `persist` dk tekrarlıysa + uzun PowerOff kesintileri → operatöre SMS/e-posta |
| `sais_domain.tasks.check_system_alarms_daily` | `10 8 * * *` | Sistem alarmları (günlük): SSL bitiş / lisans bitiş / kalibrasyon hatırlatma uyarıları |
| `api.tasks.dispatch_report_schedules` | her 60 sn | Rapor Stüdyosu: `next_run_at` vadesi gelen etkin `ReportSchedule`'ları `generate_report_run`'a gönderir (lisans gate'li) |
| `api.tasks.prune_generated_reports_task` | `45 3 * * *` | `REPORT_RETENTION_DAYS`'den (90) eski üretilen rapor kayıt + PDF/Excel dosyalarını sil |
| `api.tasks.prune_comm_errors_task` | `50 3 * * *` | `COMM_ERROR_RETENTION_DAYS`'den (30) eski `CommErrorEvent` teşhis kayıtlarını sil |

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
python manage.py resend_missing_data_once              # eksik veri yeniden gönderimini sync çalıştır
python manage.py diagnose_comm_errors --days 7         # iletişim hatası teşhisi (salt okuma)
```

## Kod düzenleme kuralları

- Mevcut dosyaların biçim stiline sadık kal (Türkçe verbose_name/help_text, snake_case tablo adları).
- **App sınırını koru:** jenerik SCADA kavramları `api/`'a, SAIS/Bakanlık/Envisoft özelinde olanlar `sais_domain/`'e gider. Yeni bir model/seed/komut eklerken hangi tarafa ait olduğunu sor.
- Veritabanı şema değişikliklerinde `python manage.py makemigrations` + `migrate` üret. Migration seti tamamen ORM-tabanlı (raw `RunSQL` yok) → PostgreSQL'de temiz uygulanır.
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

**Veri Raporu (pivot varyantı — `reports_data`):** "Ham Veri Raporu" (`reports_readings`, eski adı "Sensör
Okumaları") ile **aynı** filtre/aralık/validasyon/grafik/DataTables/FOUC iskeleti; tek fark **çıktı tablosu
pivot**: zaman satır, **her sensör ayrı sütun** ([data_report.html](dashboard/templates/dashboard/reports/data_report.html)
+ `DataReportView` — `ReadingsReportView` alt sınıfı, `_build_pivot`). Status kolonu yok; hücreler `Dinamik
Veri Raporu` mantığıyla renklenir (kod 1 sade / geçerli-ama-operasyonel amber `cell-op` / geçersiz kırmızı
`cell-bad`, `VALID_CODES`'e göre; status yoksa `Reading.quality`'e düşer; aggregate'de `bad_count` ile).
Dijital kanal 1/0 yerine **Aktif/Pasif**. Raw okumalar dakikaya yuvarlanarak (`time_iso` mikrosaniye farkları)
kovaya toplanır, aggregate'te `bucket_start` zaten hizalı. Grafik pivot sütunlarından (her sütun bir seri,
hücre `data-order`'ı sayısal) kurulur. Sütun tavanı `PIVOT_MAX_COLS=80`.

## Mimik Tasarım Stüdyosu (SCADA/HMI editör)

Yönetici → **Mimik Tasarımları** (`/dashboard/admin-pages/mimic/`) artık bir **mimik
galerisi**dir (eski "Kabin İzleme" demo SVG'sinin yerini aldı). Kullanıcılar Fabric.js tabanlı,
**dashboard iskeletinden bağımsız tam-ekran** bir editörde (yeni sekme) SCADA/HMI mimik ekranları
tasarlar. Bu faz **SCADA'ya bağlı değildir** — tasarımlar saklanır, simüle edilir, dışa aktarılır;
animasyon etiketleri (tag) serbest metindir, ileride gerçek `Sensor`/`SensorLatest`'e bağlanabilir.
Tamamen dashboard arayüzüne özgü → `dashboard/`.

- **Model** [dashboard/models.py](dashboard/models.py) `MimicScreen`: `data` (Fabric `canvas.toJSON`),
  `thumbnail` (base64 PNG galeri önizleme), `width/height/background`, `is_template` (silinemez),
  `created_by`. Migration `dashboard/0002_mimicscreen`.
- **Gerçek SCADA etiketleri**: `Sensor.tag` (api/, otomatik üretilir — `save()` boşsa parametre
  kodundan benzersiz tag türetir; migration `0025`/`0026` mevcutları doldurur). Sensör listesi +
  Jazzmin admin'de görünür. Editör tag alanı `/dashboard/api/mimic/tags/`'ten (gerçek sensör tag +
  `SensorLatest` anlık değer) datalist ile beslenir; **canlı mod** (editör sim + viewer) bu
  endpoint'i 4 sn'de bir poll'layıp animasyonları gerçek değerlerle sürer. Objeye **sağ tık** →
  hızlı tag + animasyon atama menüsü (`#ctx-menu`).
- **Yerleşik şablon**: `python manage.py seed_mimic_templates` (idempotent) "SAIS Kabini — Örnek HMI"
  şablonunu (`is_template=True`) tohumlar — eski "Kabin İzleme" demo'sunun mimik editörü formatındaki
  karşılığı (pompalar/akış hücresi/analizör paneli/yıkama tankı/debimetre/durum lambaları; Fabric JSON
  primitive + `symbolKey` ile üretilir, etiketler SAIS parametre kodlarıyla hizalı, "auto" animasyonlu).
- **View'lar** [dashboard/views.py](dashboard/views.py): `MimicDashboardView` (galeri, ListView,
  admin), `MimicEditorView` (standalone editör — `mimic/editor.html`, admin), `MimicViewerView`
  (standalone salt-okunur **canlı HMI görüntüleyici** — `mimic/viewer.html`, `RoleRequiredMixin` →
  **her rol**). Galeri kartları/butonları editör ve viewer'ı `target="_blank"` ile açar.
- **Header "Mimik" menüsü (tüm roller):** Dashboard header'ında (dil seçicinin yanında, ikon
  `ki-abstract-26`) bir dropdown — kayıtlı mimik ekranlarını listeler; her satır viewer'ı **yeni
  sekmede** açar. Liste hafif `mimic_menu` endpoint'inden (`/dashboard/api/mimic/menu/`,
  `login_required`, sadece id+ad+şablon bayrağı) AJAX ile dolar; [header.html](dashboard/templates/dashboard/partials/header.html)
  içindeki IIFE doldurur. Operatör/normal kullanıcı SCADA/HMI ekranlarını buradan izler (galeri +
  CRUD admin'de kalır).
- **Viewer = gerçek HMI deneyimi (her zaman canlı):** Simülasyon/otomatik/canlı seçimi YOK — açılışta
  doğrudan `mimic_tags`'ı 4 sn'de bir poll'layıp animasyonları **gerçek sensör (tag) değerleriyle**
  sürer. Üst bar sade: ekran adı + **canlı durum rozeti** (yeşil "Canlı" pulse / kırmızı "Bağlantı yok",
  poll başarısında güncellenir) + **saat** + Sığdır + **Tam Ekran** + (yalnız admin) Düzenle.
  **Izgara fix'i (kritik):** editör tuval arka planını kareli **grid Pattern** olarak kaydeder
  (`canvas.toJSON` → `data.background`); viewer `loadFromJSON` sonrası `canvas.setBackgroundColor`
  ile düz tasarım rengine **zorlar** → HMI'da ızgara görünmez (yalnız `.v-body` CSS'i yetmez, çünkü
  grid canvas içinde). Eski logo ikonu üst bardan kaldırıldı.
- **API** [dashboard/api_views.py](dashboard/api_views.py): galeri/CRUD `mimic_screen_list/get/save/delete`
  (`_require_admin`, CSRF'li POST) → `/dashboard/api/mimic/{list,get,save,delete}/`. Canlı veri
  `mimic_tags` + header listesi `mimic_menu` ise **tüm rollere açık** (`login_required`) — viewer her
  rolde izlenebildiğinden canlı değerler de admin-dışı kullanıcılarda çalışmalı (salt-okuma sensör
  değerleri, home snapshot gibi).
- **Tıklama aksiyon menüsü (viewer):** Bir objeye editörde `obj.scada.menu = {enabled, historic,
  report, daily, control}` (Animasyon sekmesi → "Etkileşim Menüsü") atanır — **varsayılan kapalı**.
  Viewer'da bir obje yalnız **etiketi (tag) VE `menu.enabled`** varsa tıklanabilir (`evented`);
  tıklanınca konumlu bir menü açılır: **Historik Trend (saatlik) / Veri Raporu (ham) / Günlük Özet
  (günlük)** → **viewer içinde bir modal** açar (rapor sayfasına gitmez, filtre inputu yok): salt
  tablo + DataTables export (kopyala/CSV/Excel/PDF/yazdır — arama ve sütun-seç yok) + **Grafik/Tablo
  geçiş butonu** (ApexCharts, rapor sayfası deseni; raw→değer, aggregate→ortalama). Raw tabloda
  Kalite/Durum **renkli rozet** (quality→success/danger/warning). Veri `mimic_report_data`
  endpoint'inden gelir (`?tag=&kind=raw|15min|hourly|daily`; raw→`Reading`, diğerleri aggregate
  tabloları; salt-okuma, tüm roller). **Kontrol** (yalnız operatör/admin `canControl` + dijital
  çıkış sensörü) → aktif/pasif + değer ata diyalogu → `mimic_control` endpoint'ine POST → `Command`
  (source=operator, idempotency `mimic_control:{sensor}:{val}:{bucket}`), `digital_output_command`
  ile aynı akış. `mimic_tags` artık `sensor_id/station_id/parameter_id/is_output` da döndürür.
  Viewer DataTables için `plugins.bundle.js` + `datatables.bundle.js`'i ayrıca yükler. Butonlar
  (`isButton`) menü açmaz — kendi press/release toggle'ını korur.
- **Mimik→mimik gezinme (buton):** Buton "Tıklama Davranışı" olarak **"Başka Mimik Aç"**
  (`scada.action="openMimic"`) seçilebilir; `scada.linkTarget` (hedef `MimicScreen` id, editörde
  kayıtlı mimik dropdown'u — `mimic_screen_list`'ten) + `scada.linkMode` (`modal`|`newtab`|`same`).
  Viewer'da bu butona tıklanınca hedef mimik: **sayfa içi modal** (hedef viewer'ı `<iframe>` ile —
  same-origin, `X_FRAME_OPTIONS=SAMEORIGIN`), **yeni sekme** veya **aynı sekme**de açılır. Hedef URL
  `viewerTpl` (`mimic_viewer` id=0 reverse'ü) placeholder değişimiyle kurulur. openMimic butonu
  etikete press/release yapmaz.
- **Frontend** ([dashboard/static/dashboard/js/](dashboard/static/dashboard/js/)):
  - `mimic_symbols.js` — kategorize SCADA sembol kütüphanesi (vana/pompa/motor/tank/enstrüman/
    boru/proses/elektrik SVG'leri); `window.MIMIC_SYMBOLS`.
  - `mimic_runtime.js` — animasyon/simülasyon motoru (`window.MimicRuntime`); editör önizleme +
    viewer paylaşır. Bağlama şeması obje üzerinde `obj.scada = {tag, anim, min, max, onColor,
    offColor, threshold, speed, unit, decimals, moveRange, action...}`. Genel animasyonlar:
    colorState, blink, rotate, level, fillThreshold, visibility, opacity, moveX/Y, text.
    **Sembole özel "auto"** (varsayılan; `symbolKey`'e göre `autoKind` sınıflandırır): tank/havuz
    → değere bağlı **su seviyesi + dalga** overlay, havalandırma → su + **kabarcık**, pompa/motor/
    fan → merkezde **dönen rotor** overlay, gösterge → **ibre**, boru → **akış** çizgileri, vana/
    lamba/pano → durum rengi, alarm/çakar → yanıp sönme. Overlay'ler `canvas.on("after:render")`
    ile sembolün üstüne viewport-transform uygulanmış 2D context'e çizilir (fabric grup iç yapısına
    bağımlı değil). HMI butonu (`isButton`) tıklayınca `pressButton/releaseButton` ile etiketi sürer.
  - `mimic_editor.js` — editör (tuval, zoom/pan, ızgara+snap, semboller/şekiller/resim ekleme,
    özellik+animasyon+katman panelleri, undo/redo, grup, hizalama, kaydet/yükle, PNG/SVG/JSON
    dışa+içe aktar, simülasyon). Vendored **Fabric.js 5.3** →
    `plugins/custom/fabric/fabric.min.js`.
- **Arayüz**: üst **ribbon** (Dosya/Düzen/Ekle/Sırala/Hizala/Görünüm/Dışa Aktar) — tüm araçlar
  ikon+yazılı etiket; şekiller + **Buton** (tıklanabilir HMI elemanı, `isButton`) + Resim "Ekle"
  grubunda; sol panel yalnız sembol kütüphanesi. **Tema** dashboard ile paylaşılır (localStorage
  `data-bs-theme`, varsayılan açık); editör + viewer CSS değişkenleriyle açık/koyu uyarlanır.
  Orta fare tuşu (veya Boşluk+sürükle) ile kaydırma (`fireMiddleClick`).
- **Serileştirme**: `canvas.toJSON(['scada','name','isHelper','selectable','evented'])`. Tuval
  sınırı (`boundary`) `excludeFromExport+isHelper` ile kaydedilmez, yüklemede JS yeniden kurar.
  PNG/thumbnail dışa aktarımı `withIdentityVpt` ile viewport transform sıfırlanarak yapılır (zoom/pan
  hizasızlığını önler).

## Rapor Stüdyosu (Aveva Reports benzeri raporlama editörü)

Kullanıcı **blok tabanlı rapor şablonları** tasarlar (Aveva Reports paradigması; Mimik'in aksine
serbest tuval DEĞİL — dikey akan doküman blokları), zamanlama + e-posta dağıtımı yapılandırır;
sistem raporu Celery ile otomatik üretip (PDF/Excel) alıcılara gönderir. Jenerik SCADA →
**modeller + motor + task'lar `api/`**, UI `dashboard/` (Yedekleme özelliğiyle aynı bölünme).

- **Modeller** ([api/models.py](api/models.py)): `ReportTemplate` (name + `blocks` JSONField +
  sayfa ayarları + `is_template` silinemez yerleşik), `ReportSchedule` (period daily/weekly/monthly +
  `time_of_day`/`weekday`/`day_of_month`, format bayrakları `output_pdf/excel`, e-posta
  `email_enabled/recipients/subject/body` — placeholder `{report_name}` `{date}`, dispatcher anahtarı
  `next_run_at` + `compute_next_run()`), `GeneratedReport` (iş geçmişi: status running/success/failed,
  dosyalar REPORTS_DIR altında çıplak ad, e-posta durumu; `delete()` dosyaları da siler).
- **Blok şeması** (`ReportTemplate.blocks` — sıralı liste): `heading`(text,level) / `text` /
  `kpi_cards`(cards:[{station_id,parameter_id,agg:last|avg|min|max,window,label}]) /
  `chart`(chart_type:line|bar + binding) / `table`(binding + columns + max_rows) / `page_break` /
  `spacer`. **binding** = `{station_id, parameter_ids[], bucket: raw|15min|hourly|daily, window,
  columns}`. **window** = `{"mode":"relative","key":last_24h|yesterday|last_7d|last_week|this_month|
  last_month|last_30d}` veya `{"mode":"fixed","start","end"}` — üretim anındaki `reference_time`'a
  göre çözülür (zamanlanmış raporlar her koşuda güncel dönemi kapsar).
- **Üretim motoru** [api/reporting.py](api/reporting.py): blok çözümü → veri sorgusu (parametre
  kapsam kuralı burada da geçerli: sensörler üzerinden), matplotlib (Agg) PNG grafik → HTML
  ([api/templates/reporting/report.html](api/templates/reporting/report.html), print CSS `@page`) →
  WeasyPrint PDF; openpyxl Excel (Özet + tablo/grafik sheet'leri, native chart). `generate_report()`
  backup_database komut şekli: kayıt running → üret → e-posta → success/failed. E-posta
  `api.notifications.send_email(..., kind="report", attachments=[...])` — `attachments` parametresi
  bu özellik için eklendi.
- **WeasyPrint Windows dev'de yok** (GTK/pango DLL) → import geniş `except` ile sarılı;
  `PDF_AVAILABLE=False` iken PDF atlanır (uyarı `GeneratedReport.error`'a), Excel/önizleme/e-posta
  çalışır. Docker imajı pango/cairo paketlerini içerir → sahada PDF tam çalışır.
- **Zamanlama dispatcher deseni**: schedule başına PeriodicTask YOK; tek beat task
  `api.tasks.dispatch_report_schedules` (60 sn) `next_run_at <= now` olanları kuyruklar ve
  `next_run_at`'ı kuyruklamadan ÖNCE ilerletir. Kullanıcı zamanlama düzenleyince endpoint
  `next_run_at=None` verir → model `save()` yeniden hesaplar.
- **UI**: `/dashboard/report-studio/` (`ReportStudioView`, operatör; düzenleme butonları admin) —
  **2 sekme**: Şablonlar (galeri) + Üretilen Raporlar (5 sn durum poll + indirme). Editör
  `/dashboard/report-studio/editor/[pk/]` (`ReportEditorView`, rol=1) **standalone tam-ekran**
  (mimik editörü deseni: base.html YOK, yeni sekmede açılır, Metronic CSS bundle + `--mx-*` tema
  değişkenleri, localStorage `data-bs-theme` paylaşımı): üst header (ad + Önizle/Kaydet/Stüdyo/tema),
  sol blok listesi (SortableJS), orta **canlı önizleme** (`api_report_preview` → iframe `srcdoc`,
  kaydetmeden gerçek veriyle), sağ 3 sekme: **Blok Ayarları / Sayfa / Zamanlama**. Zamanlamalar
  editörün Zamanlama sekmesinden yönetilir (şablon kaydedilmeden eklenemez); e-posta açılınca
  alıcılar **SCADA kullanıcı listesinden** seçilir (`api_notification_recipients` — e-postasız
  kullanıcılar devre dışı) + ek serbest e-posta alanı; seçim `recipients` alanına virgüllü yazılır.
  JS: [report_editor.js](dashboard/static/dashboard/js/report_editor.js).
  AJAX: `/dashboard/api/report-studio/...` (tpl list/get/save/delete, preview, sched
  list/save/delete, run-now, generated status/download/delete — download path-traversal korumalı,
  backup deseni).
- **Seed**: `python manage.py seed_report_templates` (idempotent) — "Günlük Tesis Özeti" yerleşik
  şablonu + devre dışı örnek zamanlama. **Debug**: `python manage.py generate_report
  --template-id N [--formats pdf,excel] [--schedule-id N]` (worker'sız sync üretim).
- `.env`: `REPORTS_DIR` (dev + prod compose'da `report_files` volume → `/reports`, web+worker'a
  mount; dev default `media/reports`) + `REPORT_RETENTION_DAYS` (90).
- **Retention**: `prune_generated_reports` komutu (`--days`/`--dry-run`) +
  `api.tasks.prune_generated_reports_task` (gecelik 03:45) — kayıt `delete()` dosyaları da siler.
- **Saha dağıtımı**: `docker-compose.prod.yml` + `install-linux.sh` heredoc + `env.template`
  `report_files`/`REPORTS_DIR` içerir (v0.8.1+). Mevcut sahalarda compose dosyası host'ta durduğu
  için volume ancak compose dosyası yenilenip `up -d` yapılınca gelir (Watchtower yalnız imajı günceller).
- **Ertelenenler**: EN `.po` çevirileri.

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
- ~~**Plaintext credentials** — `SaisCabinet.auth_secret` plaintext~~ **ÇÖZÜLDÜ**: artık
  [sais_domain/crypto.py](sais_domain/crypto.py) `EncryptedCharField` ile at-rest **Fernet** şifreli
  (Python'da şeffaf düz metin). Anahtar `CABINET_FERNET_KEY` (yoksa `SECRET_KEY`'den türetilir);
  migration `0012` mevcut kayıtları şifreler.
- **Per-sensor poll override yok** — `Sensor.poll_interval_sec` alanı modelde var ama dispatcher kullanmıyor; tüm sensörler bağlı oldukları connection'ın periyoduyla okunur. İleride hibrit dispatch eklenebilir. Aynı şekilde **`Sensor.timeout_ms` / `Sensor.retry_count` de ölü** (reader connection-level client kullandığından); `Connection.timeout_ms` + `Connection.retry_count` ise **pymodbus'a bağlı ve canlıdır** (aşağıdaki reader notu).
- **Aggregate Python-side** — `aggregate_readings` pandas-benzeri Python groupby kullanır; 1500+ tag ölçeğinde PostgreSQL `GROUP BY` SQL rewrite gerekir.
- **DB partition yok** — `Reading` tek tablo; 3000+ tag uzun vadeli operasyonda aylık partition (PostgreSQL declarative partitioning) gerekir.
- **Celery worker monitoring** — Flower kurulu değil; isteğe göre `pip install flower` + `celery -A sais_web flower` ile eklenebilir.
- **WeasyPrint Windows dev'de çalışmaz** — GTK/pango DLL'leri gerekir (MSYS2 veya GTK3-runtime ile kurulabilir); yoksa Rapor Stüdyosu PDF üretimi dev'de zarifçe devre dışı kalır (`api.reporting.PDF_AVAILABLE=False`), Excel/önizleme çalışır. Docker imajında sorun yok.
- **Mevcut sahalarda `report_files` volume güncellemesi manuel** — Watchtower yalnız imajı yeniler; v0.8.1 öncesi kurulmuş sahalarda `docker-compose.prod.yml` host'ta eski kaldığından Rapor Stüdyosu dosya kalıcılığı için compose dosyasının elle yenilenip `up -d` yapılması gerekir (yeni kurulumlar installer ile tam gelir).
- **Windows'ta lokal Celery** — `-B` (worker+beat tek process) desteklenmiyor; iki ayrı terminal aç veya `-P solo` ile worker + ayrı terminal'de beat. Prefork pool Windows'ta sorunlu, `-P solo` zorunlu.
- **TLS Modbus** — pymodbus 3.x henüz native desteklemiyor; out of scope.
