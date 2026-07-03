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

# İmaj build zamanı (unix epoch) — lisans bootstrap grace bu tarihe çıpalanır
# (DB created_at sıfırlamayla grace istismarını engeller). release.yml doldurur;
# verilmezse 0 → çıpasız (dev).
ARG BUILD_EPOCH=0
ENV BUILD_EPOCH=$BUILD_EPOCH

# Üretim (release) build bayrağı — 1 ise lisans enforcement kod ile mühürlenir
# (DJANGO_DEBUG=1 ile kapatılamaz). release.yml PRODUCTION=1 geçer; dev build 0.
ARG PRODUCTION=0
# OBFUSCATE=1 (release) → Cython ile seçili modüller .so'ya derlenir (bkz. aşağıda).
ARG OBFUSCATE=0

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

# Üretim build'inde lisans enforcement bayrağını kod ile sabitle (DJANGO_DEBUG=1
# bypass'ını kapatır). Cython aşaması (OBFUSCATE=1) bu dosyayı da .so'ya derler.
RUN if [ "$PRODUCTION" = "1" ]; then printf 'PRODUCTION_BUILD = True\n' > /app/api/_buildflags.py; fi

# OBFUSCATE=1 (release) → seçili iş-mantığı modüllerini Cython ile .so'ya derle,
# .py/.c kaynağını sil. build-essential + cython yalnız bu RUN içinde kurulur ve
# aynı katmanda kaldırılır → nihai imajda derleyici YOK, kaynak .py YOK (yalnız .so).
# Kaynak-koruma (kopyalama/korsanlık) tehdidine karşı asıl katman budur.
RUN if [ "$OBFUSCATE" = "1" ]; then \
        apt-get update && apt-get install -y --no-install-recommends build-essential \
        && pip install --no-cache-dir cython setuptools \
        && SETUPTOOLS_USE_DISTUTILS=local python build/cythonize_app.py --root /app \
        && pip uninstall -y cython \
        && apt-get purge -y build-essential && apt-get autoremove -y \
        && rm -rf /var/lib/apt/lists/* /root/.cache; \
    fi

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
