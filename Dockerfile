# syntax=docker/dockerfile:1.7

############################
# Builder: wheels'leri hazırla
############################
# psycopg[binary] + pandas/numpy/cryptography hepsi manylinux binary wheel ile
# gelir; derleme araçları gerekmez. build-essential yine de saf-kaynak bir
# bağımlılık çıkarsa diye güvenlik için tutulur.
FROM python:3.12-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .

RUN pip install --upgrade pip \
    && pip wheel --wheel-dir /wheels -r requirements.txt


############################
# Runtime: ince imaj (PostgreSQL client 16 ile)
############################
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SETTINGS_MODULE=sais_web.settings

# Sürüm etiketi — CI build sırasında git tag'inden doldurulur (build-arg).
# Dashboard footer'ında gösterilir; filo genelinde hangi sahanın hangi
# sürümde olduğunu görmek için kullanılır.
ARG APP_VERSION=dev
ENV APP_VERSION=$APP_VERSION

# PostgreSQL client 16 (pg_dump / pg_restore — yedekleme/geri yükleme app
# container'ından çalışır). Bookworm'un kendi paketi 15; compose `db` postgres:16
# olduğundan sürüm uyumu için PGDG deposundan client-16 kurulur (pg_dump major
# >= server major olmalı).
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl gnupg ca-certificates \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        | gpg --dearmor -o /usr/share/keyrings/pgdg.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/pgdg.gpg] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-16 \
        libpango-1.0-0 libpangocairo-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 \
        shared-mime-info fonts-dejavu-core \
    && apt-get purge -y --auto-remove curl gnupg \
    && rm -rf /var/lib/apt/lists/* \
    && addgroup --system app \
    && adduser --system --ingroup app --home /app app

WORKDIR /app

COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

COPY --chown=app:app . /app

# /backups: pg_backups named volume buraya mount edilir. Mount noktasını imajda
# app sahipliğiyle oluşturursak, ilk mount'ta named volume bu sahipliği devralır
# → non-root app kullanıcısı pg_dump çıktısını yazabilir (ayrı chmod sidecar'ı
# gerekmez).
# /reports: report_files named volume (Rapor Stüdyosu PDF/Excel çıktıları) —
# /backups ile aynı sahiplik mantığı.
RUN mkdir -p /app/staticfiles_root /app/media /backups /reports \
    && chown -R app:app /app /backups /reports

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://localhost:8000/admin/login/').status==200 else 1)" || exit 1

CMD ["gunicorn", "sais_web.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
