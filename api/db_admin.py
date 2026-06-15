"""
Veritabanı yönetim yardımcıları — yedekleme / geri yükleme için.

PostgreSQL'de `DROP DATABASE` / `CREATE DATABASE` bir transaction içinde
çalışamaz ve hedef DB'ye bağlıyken yapılamaz. Bu yüzden Django'nun default
connection'ı yerine bakım veritabanına (`postgres`) ayrı, autocommit bir
psycopg bağlantısı açarız. Asıl yedek alma/geri yükleme `pg_dump` / `pg_restore`
CLI ile yapılır (bkz. backup_database.py / restore_database.py).

Sürüm-bilinçli geri yükleme: her yedek alınırken uygulanmış migration durumu
(`{app: son_migration}`) damgalanır. Geri yüklemede `compare_schema` bu damgayı
çalışan kodun migration grafiğiyle karşılaştırıp `exact` / `forward` / `block`
verdict'i üretir.
"""
from __future__ import annotations

import os

from django.conf import settings


def maintenance_connection():
    """`postgres` bakım DB'sine autocommit psycopg bağlantısı (DROP/CREATE için).

    Bağlantı parametrelerini `settings.DATABASES['default']`'tan türetir; ama
    hedef DB'ye DEĞİL, `postgres` bakım DB'sine bağlanır (hedef DB drop/create
    edilebilsin diye). Çağıran kapatmaktan sorumludur (autocommit DDL).
    """
    import psycopg

    db = settings.DATABASES["default"]
    return psycopg.connect(
        host=db.get("HOST") or "localhost",
        port=db.get("PORT") or "5432",
        dbname="postgres",
        user=db.get("USER") or "",
        password=db.get("PASSWORD") or "",
        autocommit=True,
        connect_timeout=30,
    )


def pg_env() -> dict:
    """pg_dump / pg_restore için PGPASSWORD enjekte edilmiş ortam değişkenleri."""
    env = os.environ.copy()
    db = settings.DATABASES["default"]
    if db.get("PASSWORD"):
        env["PGPASSWORD"] = str(db["PASSWORD"])
    return env


def database_name() -> str:
    return settings.DATABASES["default"]["NAME"]


def current_migration_state() -> dict[str, str]:
    """Uygulanmış son migration'ı app başına döndürür: {app_label: migration_name}.

    Sıralama migration adının sayısal prefix'ine göre (0001_, 0002_, ...) yapılır;
    böylece "son" gerçekten en ileri migration olur.
    """
    from django.db import connection
    from django.db.migrations.recorder import MigrationRecorder

    recorder = MigrationRecorder(connection)
    applied = recorder.applied_migrations()  # {(app, name): Migration}
    state: dict[str, str] = {}
    for app, name in applied.keys():
        if app not in state or _mig_key(name) > _mig_key(state[app]):
            state[app] = name
    return state


def _mig_key(name: str):
    """Migration adını sıralanabilir anahtara çevir ('0012_foo' → (12, '0012_foo'))."""
    prefix = name.split("_", 1)[0]
    try:
        return (int(prefix), name)
    except ValueError:
        return (0, name)


def compare_schema(backup_state: dict | None) -> dict:
    """Yedeğin migration damgasını çalışan kodla karşılaştır.

    Dönen dict:
      verdict: 'exact' | 'forward' | 'block'
      message: kullanıcıya gösterilecek açıklama
      missing: kodda bulunmayan (backup'ta olan) migration'lar — block sebebi
      ahead:   kodun backup'tan ileride olduğu app'ler — forward sebebi

    Mantık:
      - backup_state boşsa (eski/damgasız yedek) → forward (temkinli; migrate dene).
      - backup'taki herhangi bir migration adı kodun disk migration'larında YOKSA
        → block (yedek daha yeni veya farklı bir branch'ten).
      - aksi halde: backup ⊆ kod. Kod, backup'ın ötesinde migration içeriyorsa
        → forward; tam eşitse → exact.
    """
    from django.db import connection
    from django.db.migrations.loader import MigrationLoader

    if not backup_state:
        return {
            "verdict": "forward",
            "message": "Yedeğin sürüm damgası yok; geri yükleme sonrası migrate denenecek.",
            "missing": [],
            "ahead": [],
        }

    loader = MigrationLoader(connection, ignore_no_migrations=True)
    # Disk'teki tüm migration adları, app başına set.
    disk_by_app: dict[str, set[str]] = {}
    for app, name in loader.disk_migrations.keys():
        disk_by_app.setdefault(app, set()).add(name)

    missing = []
    ahead = []
    for app, backup_name in backup_state.items():
        names = disk_by_app.get(app, set())
        if backup_name not in names:
            missing.append(f"{app}.{backup_name}")
            continue
        # Kod bu app'te backup'tan ileri bir migration içeriyor mu?
        backup_key = _mig_key(backup_name)
        if any(_mig_key(n) > backup_key for n in names):
            ahead.append(app)

    if missing:
        return {
            "verdict": "block",
            "message": (
                "Bu yedek, çalışan kodda bulunmayan migration'lar içeriyor "
                "(daha yeni bir sürümde alınmış). Önce uygulamayı o sürüme yükseltin "
                "(.env IMAGE_TAG=<yedeğin sürümü> → up -d), sonra geri yükleyin. "
                f"Eksik: {', '.join(missing)}"
            ),
            "missing": missing,
            "ahead": ahead,
        }

    if ahead:
        return {
            "verdict": "forward",
            "message": (
                "Yedek, çalışan koddan daha eski bir şemaya ait. Geri yükleme sonrası "
                "şema otomatik 'migrate' ile güncel sürüme taşınacak."
            ),
            "missing": [],
            "ahead": ahead,
        }

    return {
        "verdict": "exact",
        "message": "Yedeğin şeması çalışan kodla birebir uyumlu; migrate gerekmez.",
        "missing": [],
        "ahead": [],
    }
