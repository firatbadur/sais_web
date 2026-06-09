# syntax=docker/dockerfile:1.7

############################
# Builder: wheels'leri hazırla
############################
# NOT: bookworm'a sabitli — Debian 13 (trixie) için Microsoft ODBC (msodbcsql18)
# prod.list deposu henüz yok; "slim" trixie'ye kayınca build kırılıyor.
FROM python:3.12-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        unixodbc-dev \
        gcc \
        g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .

RUN pip install --upgrade pip \
    && pip wheel --wheel-dir /wheels -r requirements.txt


############################
# Runtime: ince imaj (MSSQL ODBC Driver 18 ile)
############################
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SETTINGS_MODULE=sais_web.settings \
    ACCEPT_EULA=Y

# Sürüm etiketi — CI build sırasında git tag'inden doldurulur (build-arg).
# Dashboard footer'ında gösterilir; filo genelinde hangi sahanın hangi
# sürümde olduğunu görmek için kullanılır.
ARG APP_VERSION=dev
ENV APP_VERSION=$APP_VERSION

# Microsoft ODBC Driver 18 for SQL Server (Debian 12 / bookworm).
# NOT: prod.list'i çekip sed'lemek yerine source satırını DOĞRUDAN yazıyoruz —
# Microsoft prod.list formatını değiştirdi ve curl+sed "Malformed entry (URI
# parse)" ile kırılıyordu. Taban imaj bookworm'a sabit olduğundan değerler sabit.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl gnupg ca-certificates \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
        | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && echo "deb [arch=amd64,arm64,armhf signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
        > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends \
        msodbcsql18 \
        unixodbc \
        libodbc2 \
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

RUN mkdir -p /app/staticfiles_root /app/media \
    && chown -R app:app /app

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
