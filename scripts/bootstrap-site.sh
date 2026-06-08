#!/usr/bin/env bash
# ── Yeni saha açılış scripti (Linux) ────────────────────────────────────────
# Bir SCADA istasyonunu sıfırdan ayağa kaldırır:
#   1. GHCR'a login (image'ları çekebilmek için)
#   2. Image'ları çek + container'ları başlat (db/redis/web/worker/beat + watchtower)
#   3. İLK kurulumda bir kez veri tohumlama (seed_initial_data + seed_sais_data)
#
# Ön koşul: Docker + docker compose kurulu; bu dizinde docker-compose.prod.yml ve
# saha-özel doldurulmuş .env mevcut.
#
# Kullanım:
#   ./scripts/bootstrap-site.sh                # normal başlatma
#   FIRST_RUN=1 ./scripts/bootstrap-site.sh    # ilk kurulum (veri tohumlama dahil)
set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE="docker compose -f docker-compose.prod.yml --env-file .env"

if [[ ! -f .env ]]; then
  echo "HATA: .env bulunamadı. .env.example'dan kopyalayıp saha-özel doldurun." >&2
  exit 1
fi

# 1) GHCR login — image private ise gerekli. read:packages kapsamlı PAT kullan.
#    Bir kez yapılır; ~/.docker/config.json'a yazılır (Watchtower da bunu kullanır).
if ! grep -q "ghcr.io" "${HOME}/.docker/config.json" 2>/dev/null; then
  echo ">> GHCR'a login olun (username = GitHub kullanıcı adı, password = read:packages PAT):"
  docker login ghcr.io
fi

# 2) Çek + başlat
echo ">> Image'lar çekiliyor..."
$COMPOSE pull
echo ">> Container'lar başlatılıyor..."
$COMPOSE up -d

# 3) İlk kurulum: çekirdek + SAIS veri tohumlama (idempotent ama bir kez yeterli).
#    Periodic task'lar zaten web başlangıcında seed_periodic_tasks ile yazılıyor.
if [[ "${FIRST_RUN:-0}" == "1" ]]; then
  echo ">> İlk kurulum: veri tohumlanıyor (web container'ında)..."
  $COMPOSE exec -T web python manage.py seed_initial_data
  $COMPOSE exec -T web python manage.py seed_sais_data
  echo ">> Bir superuser oluşturun:"
  $COMPOSE exec web python manage.py createsuperuser
  echo ">> Sonraki adım: admin panelinden bu sahaya özel Station / Connection /"
  echo "   Sensor ve SaisCabinet (Bakanlık SIM ID) kayıtlarını girin."
fi

echo ">> Tamamlandı. Durum:"
$COMPOSE ps
