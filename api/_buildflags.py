# Build-time üretim bayrağı — lisans enforcement'ını Django DEBUG'dan AYIRIR.
#
# Repoda False'tur (dev). Release imaj build'inde Dockerfile bu dosyayı
# `PRODUCTION_BUILD = True` ile ÜZERİNE YAZAR (ARG PRODUCTION=1) ve Cython ile
# derlenerek mühürlenir. Böylece dağıtılan imajda `DJANGO_DEBUG=1` ile lisans
# enforcement'ını kapatma vektörü kapanır (settings.LICENSE_ENFORCE bunu OR'lar).
#
# Dev / lokal build (docker-compose.yml, PRODUCTION geçmez) → False kalır, DEBUG
# ile enforce kapalı, lokal geliştirme kilitlenmez.
PRODUCTION_BUILD = False
