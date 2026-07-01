#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  Envisoft WebX — Linux tek-komut kurulum
#  (Windows "next-next-next" installer'ının Linux karşılığı)
#
#  Windows installer neyi otomatik yapıyorsa bunun karşılığını yapar:
#    1. Docker CE kurar (yoksa)
#    2. .env üretir + DJANGO_SECRET_KEY / PostgreSQL şifresi rastgele oluşturur
#    3. docker-compose.prod.yml'yi yazar (self-contained — repo gerekmez)
#    4. GHCR'a login olur, image'ları çeker, yığını başlatır
#    5. İlk veriyi tohumlar (seed_initial_data + seed_sais_data) + admin oluşturur
#    6. systemd servisi kaydeder → sunucu her açıldığında yığın otomatik kalkar
#
#  Kullanım (interaktif — sorular sorar):
#    sudo bash install-linux.sh
#
#  Kullanım (unattended — env değişkenleriyle, soru sormaz):
#    sudo DOMAIN=sais-tesis1.envisoft.com.tr \
#         ADMIN_USER=admin ADMIN_PASS='...' ADMIN_EMAIL=admin@firma.com \
#         GHCR_USER=firatbadur GHCR_TOKEN='ghp_...' \
#         bash install-linux.sh
#
#  NOT: Alan adı (DOMAIN) yalnız ALLOWED_HOSTS/CSRF wildcard'ı için istenir;
#  SSL/domain'i sonradan dashboard → Yönetici → Web Erişim Ayarları'ndan
#  açacağın için burada Caddy'ye domain yazılmaz (IP ile HTTP çalışır).
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Ayarlanabilir varsayılanlar (env ile override edilebilir) ────────────────
INSTALL_DIR="${INSTALL_DIR:-/opt/envisoft}"
IMAGE_TAG="${IMAGE_TAG:-stable}"
SERVICE_NAME="${SERVICE_NAME:-envisoft-webx}"
COMPOSE_FILE="docker-compose.prod.yml"

# ── Build-time gömülü GHCR kimliği (release.yml doldurur) ─────────────────────
# MÜŞTERİYE dağıtılan sürümde bu placeholder'lar CI'da gerçek değerlerle değiştirilir
# (Windows installer'daki gömülü token'ın karşılığı) → müşteri GHCR/GitHub token
# GİRMEZ, sadece `bash install-linux.sh` çalıştırır. Repo'dan (geliştirici) çalışınca
# placeholder kalır → aşağıda boşa sayılır → yalnız o zaman interaktif sorulur.
EMBED_GHCR_USER='__GHCR_USER__'
EMBED_GHCR_TOKEN='__GHCR_TOKEN__'
EMBED_GHCR_IMAGE='__GHCR_IMAGE__'
case "$EMBED_GHCR_USER"  in *__GHCR_USER__*)  EMBED_GHCR_USER="";;  esac
case "$EMBED_GHCR_TOKEN" in *__GHCR_TOKEN__*) EMBED_GHCR_TOKEN="";; esac
case "$EMBED_GHCR_IMAGE" in *__GHCR_IMAGE__*) EMBED_GHCR_IMAGE="";; esac

GHCR_IMAGE="${GHCR_IMAGE:-${EMBED_GHCR_IMAGE:-ghcr.io/firatbadur/sais_web}}"
GHCR_USER="${GHCR_USER:-${EMBED_GHCR_USER:-firatbadur}}"
GHCR_TOKEN="${GHCR_TOKEN:-$EMBED_GHCR_TOKEN}"

# ── Renkli çıktı yardımcıları ────────────────────────────────────────────────
c_reset='\033[0m'; c_cyan='\033[36m'; c_green='\033[32m'; c_yellow='\033[33m'; c_red='\033[31m'
step() { echo -e "\n${c_cyan}>> $*${c_reset}"; }
ok()   { echo -e "   ${c_green}[OK]${c_reset} $*"; }
warn() { echo -e "   ${c_yellow}[!]${c_reset} $*"; }
die()  { echo -e "\n${c_red}[HATA]${c_reset} $*" >&2; exit 1; }

# ── Rastgele secret üretici (harf/rakam + birkaç sembol) ─────────────────────
# NOT: `head -c N` boruyu erken kapatır → `tr`'ye SIGPIPE (141) → pipefail+set -e
# script'i öldürür. `|| true` ile pipeline'ın SIGPIPE çıkışını yut (çıktı zaten alındı).
gen_secret() { LC_ALL=C tr -dc 'A-Za-z0-9!@%^_=+-' </dev/urandom 2>/dev/null | head -c "${1:-50}" || true; }

# ── Root kontrolü ─────────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || die "Bu script root olarak çalışmalı. 'sudo bash install-linux.sh' deneyin."

# ── İnteraktif mi? (TTY var mı) ──────────────────────────────────────────────
INTERACTIVE=0
[[ -t 0 ]] && INTERACTIVE=1

ask() {  # ask VAR "Soru" "varsayilan"
    local __var="$1" __q="$2" __def="${3:-}" __cur="${!1:-}" __ans
    [[ -n "$__cur" ]] && return 0                       # env ile geldiyse dokunma
    if [[ $INTERACTIVE -eq 0 ]]; then
        [[ -n "$__def" ]] && { printf -v "$__var" '%s' "$__def"; return 0; }
        die "'$__var' değeri gerekli (unattended modda env ile verin)."
    fi
    if [[ -n "$__def" ]]; then read -rp "   $__q [$__def]: " __ans; else read -rp "   $__q: " __ans; fi
    printf -v "$__var" '%s' "${__ans:-$__def}"
}
ask_secret() {  # ask_secret VAR "Soru"
    local __var="$1" __q="$2" __cur="${!1:-}" __ans
    [[ -n "$__cur" ]] && return 0
    [[ $INTERACTIVE -eq 0 ]] && die "'$__var' değeri gerekli (unattended modda env ile verin)."
    read -rsp "   $__q: " __ans; echo; printf -v "$__var" '%s' "$__ans"
}

echo -e "${c_cyan}==== Envisoft WebX — Linux Kurulum ====${c_reset}"
echo    "Kurulum dizini: $INSTALL_DIR"

# ── Kurulum sorularını topla ─────────────────────────────────────────────────
# GHCR kullanıcı/token gömülüyse (müşteri sürümü) SORULMAZ. ask_secret zaten
# değişken doluysa atlar — yani token yalnız geliştirici repo sürümünde sorulur.
step "Kurulum bilgileri"
ask        DOMAIN      "Alan adı (SSL'i sonra dashboard'dan ayarlayacaksınız; boş bırakılabilir)" ""
ask        ADMIN_USER  "Yönetici (admin) kullanıcı adı" "admin"
ask_secret ADMIN_PASS  "Yönetici şifresi"
ask        ADMIN_EMAIL "Yönetici e-postası" ""
ask        LICENSE_KEY "Lisans anahtarı (yoksa boş geçin — lisans zorlaması kapalı kalır)" ""
ask        LICENSE_URL "Lisans manifest URL'i (yoksa boş geçin)" ""
# GHCR token yalnız gömülü DEĞİLSE (geliştirici) sorulur:
ask_secret GHCR_TOKEN  "GHCR token (read:packages yetkili PAT)"

[[ -n "${GHCR_TOKEN:-}" ]] || die "GHCR token bulunamadı (gömülü değil ve girilmedi)."
[[ -n "${ADMIN_PASS:-}"  ]] || die "Yönetici şifresi boş olamaz."

# ── Adım 1: Docker CE ─────────────────────────────────────────────────────────
step "[1/6] Docker denetleniyor / kuruluyor"
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    ok "Docker + compose zaten kurulu ($(docker --version | awk '{print $3}' | tr -d ,))"
else
    echo "   Docker kuruluyor (get.docker.com)..."
    curl -fsSL https://get.docker.com | sh >/dev/null
    docker compose version >/dev/null 2>&1 || die "Docker compose kurulamadı."
    ok "Docker kuruldu."
fi
systemctl enable --now docker >/dev/null 2>&1 || true

DOCKER_BIN="$(command -v docker)"

# ── Adım 2: .env + compose dosyalarını yaz ───────────────────────────────────
step "[2/6] Yapılandırma üretiliyor ($INSTALL_DIR)"
mkdir -p "$INSTALL_DIR"

SECRET="$(gen_secret 50)"
PG_PASSWORD="${POSTGRES_PASSWORD:-$(gen_secret 28)}"
WT_TOKEN="${WATCHTOWER_API_TOKEN:-$(gen_secret 24)}"

# Sunucunun birincil IP'si (ALLOWED_HOSTS + son mesaj için)
SERVER_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"; SERVER_IP="${SERVER_IP:-127.0.0.1}"

# Domain'den fleet-wide wildcard türet: sais-tesis1.envisoft.com.tr -> .envisoft.com.tr
ALLOWED_HOSTS="localhost,127.0.0.1,web,${SERVER_IP}"
CSRF_ORIGINS="http://localhost,http://127.0.0.1,http://${SERVER_IP}"
if [[ -n "$DOMAIN" ]]; then
    if [[ "$DOMAIN" == *.*.* ]]; then BASE_DOMAIN=".${DOMAIN#*.}"; else BASE_DOMAIN=".$DOMAIN"; fi
    ALLOWED_HOSTS="${ALLOWED_HOSTS},${BASE_DOMAIN}"
    CSRF_ORIGINS="${CSRF_ORIGINS},https://*${BASE_DOMAIN},https://${DOMAIN}"
fi

# Lisans anahtarı verildiyse zorlamayı aç, yoksa kapalı bırak (kilitlenmesin).
if [[ -n "$LICENSE_KEY" ]]; then LICENSE_ENFORCE=1; else LICENSE_ENFORCE=0; fi

# .env — normal heredoc (kabuk değişkenleri genişler). Secret'lar $/`/" içermez.
cat > "$INSTALL_DIR/.env" <<EOF
# Envisoft WebX .env — install-linux.sh tarafından üretildi
GHCR_IMAGE=${GHCR_IMAGE}
IMAGE_TAG=${IMAGE_TAG}
WATCHTOWER_POLL_INTERVAL=300
WATCHTOWER_API_TOKEN=${WT_TOKEN}
# GHCR login root altında yapılır -> Watchtower bu config'i mount eder.
DOCKER_CONFIG_DIR=/root/.docker

# Django
DJANGO_SECRET_KEY=${SECRET}
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=${ALLOWED_HOSTS}
DJANGO_CSRF_TRUSTED_ORIGINS=${CSRF_ORIGINS}
DJANGO_LANGUAGE_CODE=tr
DJANGO_TIME_ZONE=Europe/Istanbul
DJANGO_USE_TZ=1
DJANGO_LOG_LEVEL=INFO

# Web erişim / Caddy (domain + SSL dashboard'dan yönetilir)
CADDY_CONFIG_PATH=/etc/caddy/Caddyfile
CADDY_CERT_DIR=/etc/caddy/certs
CADDY_UPSTREAM=web:8000

# PostgreSQL (bundled container)
POSTGRES_DB=envisoft
POSTGRES_USER=envisoft
POSTGRES_PASSWORD=${PG_PASSWORD}
POSTGRES_HOST=db
POSTGRES_PORT=5432
DB_CONN_MAX_AGE=60

# Redis / Celery
REDIS_URL=redis://redis:6379/1
CELERY_BROKER_URL=redis://redis:6379/2
CELERY_RESULT_BACKEND=django-db

# API log + retention
API_LOG_ENABLED=1
API_LOG_RETENTION_DAYS=90
READING_RETENTION_RAW_DAYS=90
READING_RETENTION_15M_DAYS=365
READING_RETENTION_HOURLY_DAYS=1825
READING_RETENTION_DAILY_DAYS=99999

# DB yedekleme
BACKUP_DIR=/backups

# Lisanslama
LICENSE_ENFORCE=${LICENSE_ENFORCE}
LICENSE_KEY=${LICENSE_KEY}
LICENSE_URL=${LICENSE_URL}
LICENSE_WARN_DAYS=15
LICENSE_BOOTSTRAP_GRACE_HOURS=168
MACHINE_FINGERPRINT=

# Session / production
SESSION_COOKIE_AGE=36000
SECURE_HSTS_SECONDS=31536000
DJANGO_SECURE_SSL_REDIRECT=0

# Bakanlık SAIS + Envisoft entegrasyonu (gerekirse admin'den güncelle)
SAIS_SIM_BASE_URL=https://entegrationsais.csb.gov.tr
SAIS_SIM_SOFTWARE_VERSION=EnvisoftV.2
SAIS_SIM_TICKET_TTL=3600
ENVISOFT_DIAGNOSTIC_URL=https://scada.onlinecevre.com.tr/sais-get-diagnostic/
ENVISOFT_SEND_DATA_URL=https://entegration.onlinecevre.com.tr/SendData
ENVISOFT_USERNAME=envisoft
ENVISOFT_PASSWORD=change-me
ENVISOFT_VERIFY_TLS=0
EOF
chmod 600 "$INSTALL_DIR/.env"
ok ".env üretildi (secret + PostgreSQL şifresi rastgele)."

# docker-compose.prod.yml — quoted heredoc (literal; ${..}/$$.. genişlemez)
cat > "$INSTALL_DIR/$COMPOSE_FILE" <<'COMPOSE_EOF'
name: sais_web

x-app-image: &app-image ${GHCR_IMAGE:-ghcr.io/firatbadur/sais_web}:${IMAGE_TAG:-stable}

x-app-env: &app-env
  POSTGRES_HOST: db
  POSTGRES_PORT: 5432
  REDIS_URL: redis://redis:6379/1
  CELERY_BROKER_URL: redis://redis:6379/2
  CELERY_RESULT_BACKEND: django-db
  WATCHTOWER_API_TOKEN: "${WATCHTOWER_API_TOKEN:-envisoft-update-token}"

services:
  caddy_config_init:
    image: busybox
    command: sh -c "mkdir -p /etc/caddy/certs && chmod -R 0777 /etc/caddy"
    volumes:
      - caddy_config:/etc/caddy

  db:
    image: postgres:16
    restart: unless-stopped
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-envisoft}
      POSTGRES_USER: ${POSTGRES_USER:-envisoft}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-envisoft}
    volumes:
      - pg_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]
      interval: 15s
      timeout: 10s
      retries: 10
      start_period: 20s

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    command: ["redis-server", "--appendonly", "yes"]
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  web:
    image: *app-image
    restart: unless-stopped
    env_file: .env
    environment:
      <<: *app-env
      DJANGO_ALLOWED_HOSTS: ${DJANGO_ALLOWED_HOSTS:-localhost,127.0.0.1,web}
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
      caddy_config_init:
        condition: service_completed_successfully
    volumes:
      - media_data:/app/media
      - static_data:/app/staticfiles_root
      - pg_backups:/backups
      - caddy_config:/etc/caddy
    expose:
      - "8000"
    labels:
      - "com.centurylinklabs.watchtower.enable=true"
    command: >
      sh -c "python manage.py ensure_database &&
             python manage.py migrate --noinput &&
             (python manage.py detect_power_off || true) &&
             python manage.py collectstatic --noinput &&
             python manage.py seed_periodic_tasks &&
             (python manage.py refresh_license || true) &&
             (python manage.py render_caddyfile || true) &&
             gunicorn sais_web.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 60 --access-logfile - --error-logfile -"

  caddy:
    image: caddy:2
    restart: unless-stopped
    depends_on:
      web:
        condition: service_started
      caddy_config_init:
        condition: service_completed_successfully
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - caddy_config:/etc/caddy
      - caddy_data:/data
    command: caddy run --config /etc/caddy/Caddyfile --watch

  celery_worker:
    image: *app-image
    restart: unless-stopped
    env_file: .env
    environment: *app-env
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
      web:
        condition: service_started
    labels:
      - "com.centurylinklabs.watchtower.enable=true"
    volumes:
      - pg_backups:/backups
    healthcheck:
      disable: true
    command: >
      sh -c "until python manage.py migrate --check; do echo 'migration bekleniyor...'; sleep 3; done &&
             celery -A sais_web worker -l info --concurrency=4"

  celery_beat:
    image: *app-image
    restart: unless-stopped
    env_file: .env
    environment: *app-env
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
      web:
        condition: service_started
    labels:
      - "com.centurylinklabs.watchtower.enable=true"
    healthcheck:
      disable: true
    command: >
      sh -c "until python manage.py migrate --check; do echo 'migration bekleniyor...'; sleep 3; done &&
             celery -A sais_web beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler"

  watchtower:
    image: containrrr/watchtower:1.7.1
    restart: unless-stopped
    environment:
      DOCKER_API_VERSION: "1.43"
      WATCHTOWER_LABEL_ENABLE: "true"
      WATCHTOWER_CLEANUP: "true"
      WATCHTOWER_HTTP_API_UPDATE: "true"
      WATCHTOWER_HTTP_API_TOKEN: "${WATCHTOWER_API_TOKEN:-envisoft-update-token}"
      TZ: ${DJANGO_TIME_ZONE:-Europe/Istanbul}
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ${DOCKER_CONFIG_DIR:-/root/.docker}/config.json:/config.json:ro

volumes:
  pg_data:
  pg_backups:
  redis_data:
  media_data:
  static_data:
  caddy_config:
  caddy_data:
COMPOSE_EOF
ok "docker-compose.prod.yml yazıldı."

COMPOSE="$DOCKER_BIN compose -f $INSTALL_DIR/$COMPOSE_FILE --env-file $INSTALL_DIR/.env"

# ── Adım 3: GHCR login ────────────────────────────────────────────────────────
step "[3/6] GHCR'a login olunuyor ($GHCR_USER)"
echo "$GHCR_TOKEN" | "$DOCKER_BIN" login ghcr.io -u "$GHCR_USER" --password-stdin >/dev/null \
    || die "GHCR login başarısız. Kullanıcı adı / token'ı (read:packages) kontrol edin."
ok "GHCR login başarılı."

# ── Adım 4: Pull + up ─────────────────────────────────────────────────────────
step "[4/6] Image'lar çekiliyor + yığın başlatılıyor (birkaç dakika sürebilir)"
$COMPOSE pull
$COMPOSE up -d
ok "Container'lar başlatıldı."

# ── Adım 5: Migration bekle + ilk veri + admin ───────────────────────────────
step "[5/6] Migration bekleniyor + ilk veri tohumlanıyor"
deadline=$(( $(date +%s) + 480 ))
until $COMPOSE exec -T web python manage.py migrate --check >/dev/null 2>&1; do
    [[ $(date +%s) -gt $deadline ]] && die "Web container migration'ları zamanında bitiremedi. Log: $COMPOSE logs web"
    echo "   migration bekleniyor..."; sleep 5
done
ok "Şema hazır."

seed_retry() {  # seed_retry "Açıklama" cmd...
    local desc="$1"; shift
    for i in 1 2 3; do
        if $COMPOSE exec -T web "$@" >/dev/null 2>&1; then ok "$desc"; return 0; fi
        sleep 3
    done
    die "$desc — başarısız oldu."
}
seed_retry "Çekirdek veri (seed_initial_data)" python manage.py seed_initial_data
seed_retry "SAIS verisi (seed_sais_data)"      python manage.py seed_sais_data

echo "   Admin kullanıcısı oluşturuluyor ($ADMIN_USER)..."
$COMPOSE exec -T \
    -e DJANGO_SUPERUSER_USERNAME="$ADMIN_USER" \
    -e DJANGO_SUPERUSER_EMAIL="$ADMIN_EMAIL" \
    -e DJANGO_SUPERUSER_PASSWORD="$ADMIN_PASS" \
    web python manage.py seed_admin_user >/dev/null 2>&1 \
    || die "Admin kullanıcısı oluşturulamadı."
ok "Admin kullanıcısı hazır ($ADMIN_USER)."

# ── Adım 6: systemd servisi (açılışta otomatik başlatma) ─────────────────────
step "[6/6] Otomatik başlatma servisi kaydediliyor ($SERVICE_NAME)"
cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=Envisoft WebX SCADA stack (docker compose)
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${INSTALL_DIR}
ExecStart=${DOCKER_BIN} compose -f ${COMPOSE_FILE} --env-file .env up -d
ExecStop=${DOCKER_BIN} compose -f ${COMPOSE_FILE} --env-file .env down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null 2>&1
ok "Servis kaydedildi — sunucu her açıldığında yığın otomatik kalkar."

# ── Özet ──────────────────────────────────────────────────────────────────────
echo -e "\n${c_green}==== KURULUM TAMAMLANDI ====${c_reset}"
echo    "Dashboard (IP)   : http://${SERVER_IP}/dashboard/"
[[ -n "$DOMAIN" ]] && echo "Dashboard (domain): https://${DOMAIN}/dashboard/  (SSL'i dashboard'dan açın)"
echo    "Yönetici kullanıcı: ${ADMIN_USER}"
echo    "Kurulum dizini    : ${INSTALL_DIR}  (.env burada — şifreler içinde)"
echo -e "\nSonraki adımlar:"
echo    "  1) Tarayıcıdan http://${SERVER_IP}/dashboard/ ile giriş yapın."
echo    "  2) Yönetici → Web Erişim Ayarları'ndan domain + SSL'i açın."
echo    "  3) Admin panelinden Station / Connection / Sensor / SaisCabinet kayıtlarını girin."
echo -e "\nYönetim komutları:"
echo    "  cd ${INSTALL_DIR}"
echo    "  ${DOCKER_BIN} compose -f ${COMPOSE_FILE} --env-file .env ps       # durum"
echo    "  ${DOCKER_BIN} compose -f ${COMPOSE_FILE} --env-file .env logs -f  # loglar"
echo    "  systemctl status ${SERVICE_NAME}                                  # servis"
