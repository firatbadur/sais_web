# sais_web — Proje Notları

Bu dosya Claude Code için proje rehberidir. Geliştirmeye başlamadan önce okunması önerilir.

## Proje amacı

`sais_web`, **atıksu sürekli izleme** istasyonlarından (SAIS) gelen ölçüm verilerini toplayan, saklayan ve REST API ile sunan bir **web SCADA uygulamasıdır**. Django 5.2 + Django REST Framework üzerine kuruludur.

Proje atıksu odaklı olmakla birlikte, mimari **her türlü endüstriyel izleme/SCADA senaryosuna** uyarlanabilir olacak şekilde geneldir:

- Modbus TCP / RTU cihazlarıyla haberleşme (`modbus/reader.py`)
- Çok istasyonlu (multi-station) yapı (`StationInfo`, `SimInformation`)
- Parametre/sensör/status kod şemaları veri modelinde ayrık
- Numune alma senaryosu ve digital output tetikleme (`Out_Requests`)
- Kalibrasyon ve power-off (açılma/kapanma) kayıtları
- Sistem ve API log tabloları

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
| Veri işleme | pandas, numpy |
| Prod sunucu | gunicorn |
| Container | Docker + docker-compose |

## Proje yapısı

```
sais_web/
├── sais_web/              # Django project root (settings, urls, wsgi, asgi)
├── api/                   # Ana uygulama: istasyon, parametre, sensör, reads
│   ├── models.py          # StationInfo, Parameters, Sensors, Reads vs.
│   ├── views.py           # REST API endpoint'leri
│   ├── serializers.py
│   ├── signals.py         # SIGTERM/SIGINT yakalar, Poweroff kaydı kapatır
│   ├── helpers.py         # DataFrame → JSON safe dönüşüm
│   ├── management/commands/seed_initial_data.py
│   └── migrations/
├── users/                 # CustomUser (rol tabanlı)
├── modbus/                # pymodbus reader + numune alma
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
python manage.py seed_initial_data
python manage.py createsuperuser
python manage.py runserver
```

### Docker ile

```bash
docker compose up -d --build
docker compose exec web python manage.py migrate
docker compose exec web python manage.py seed_initial_data
docker compose exec web python manage.py createsuperuser
```

Servisler: `web` (Django), `db` (Postgres 16), `redis` (Redis 7).

## Önemli çalıştırma davranışları

- **Yapılandırma:** Tüm ayarlar `.env` üzerinden okunur (`python-dotenv` + `dj-database-url`). Sırları asla koda commitlemeyin.
- **DEBUG=False** modunda HSTS, güvenli çerez ve Whitenoise manifest storage aktiftir.
- **Seed komutu idempotenttir** — birden çok kez çalıştırmak güvenlidir (`get_or_create`).
- **Modbus okuma:** `modbus.reader.ModbusReader` TCP bağlantıları ThreadPool ile paralel, serial bağlantıları sırayla okur. Periyodik çalıştırma için bir management command veya scheduler (ör. Celery/APScheduler) önerilir — mevcut repo bunu içermiyor.
- **Numune alma:** `StartSampleView` → `Out_Requests` tablosuna kayıt bırakır. Gerçek PLC/RTU komutunu gönderen aşamanın (TODO) ayrıca implement edilmesi gerekir.

## Kod düzenleme kuralları

- Mevcut dosyaların biçim stiline sadık kal (Türkçe verbose_name/help_text, snake_case tablo adları).
- Veritabanı şema değişikliklerinde `python manage.py makemigrations` + `migrate` üret.
- Yeni secret/ayar eklerken hem `.env.example` hem `settings.py`'yi güncelle.
- `api/views.py` — **Response sözleşmesi** tüm endpoint'lerde aynı: `{"result": bool, "message": str | null, "objects": any}`. Yeni endpoint eklerken bu formatı koru.
- REST endpoint default `IsAuthenticated`; public uç nokta eklenirken `permission_classes` açıkça override edilmeli.
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

- Modbus reader'ı tetikleyen bir zamanlayıcı yok (Celery beat / APScheduler eklenebilir).
- `api/urls.py` boş; tüm SIM endpoint'leri `sais_web/urls.py` içinde tanımlı. Genişlerken `api/urls.py`'ye taşımak temiz olur.
- `users` uygulamasının `views.py`'si minimal. Rol bazlı ön yüz akışı ileride eklenecek.
- `pymodbus 3.x` API'sinde `method=` parametresi ve `unit=` yerine `slave=` kullanımı var — `modbus/reader.py` bu değişikliklere göre güncellenmeli (TODO).
