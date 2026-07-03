#!/usr/bin/env python3
"""Seçili iş-mantığı modüllerini Cython ile .so'ya derleyip .py/.c kaynağını siler.

Docker release build'inde (OBFUSCATE=1) /app WORKDIR'inde çalışır. Yalnız EXPLICIT
COMPILE listesindeki modüller derlenir — Django introspection/entry-point/isim-tabanlı
keşif gerektirenler (migrations, models, apps, __init__, management, templatetags,
manage/wsgi/asgi/celery/settings/urls) DIŞARIDA bırakılır (bkz. plan).

.so modül adını korur -> Django app/task/templatetag/command keşfi bozulmaz.
`--dry-run` ile derlemeden yalnız hedef listesini yazdırır (yerelde gcc olmadan test).

Kullanım:
    python build/cythonize_app.py [--dry-run] [--root /app]
"""
from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys

# İş mantığı — DERLENECEK (öncelik: lisans çekirdeği + Bakanlık protokolü + SCADA IO).
# Genişletmeye açık; riskli modüller (views/api_views/tasks/serializers/middleware/
# models) ilk turda BİLİNÇLİ olarak DIŞARIDA — sağlamlaştıkça eklenir.
COMPILE_GLOBS = [
    # Lisans çekirdeği (en kritik — patch'lenirse tüm enforcement düşer)
    "api/licensing.py",
    "api/_buildflags.py",
    # Bakanlık + Envisoft protokol istemcileri (ticari değeri yüksek)
    "sais_domain/clients/sim.py",
    "sais_domain/clients/envisoft.py",
    # SAIS iş mantığı
    "sais_domain/services.py",
    "sais_domain/scenario_engine.py",
    "sais_domain/system_alarms.py",
    "sais_domain/sim_report.py",
    "sais_domain/crypto.py",
    # SCADA IO çekirdeği
    "scada_io/decoders.py",
    "scada_io/persistence.py",
    "scada_io/connection_pool.py",
    "scada_io/pool_control.py",
    "scada_io/readers/*.py",
    "scada_io/writers/*.py",
    # api yardımcı iş mantığı
    "api/reporting.py",
    "api/notifications.py",
    "api/web_proxy.py",
    "api/api_logging.py",
    "api/db_admin.py",
    "api/connection_io.py",
    "api/events.py",
    "api/config_migration.py",
    "api/helpers.py",
    "api/exception_handlers.py",
    "api/alarms.py",
    "api/alarm_autocreate.py",
    # dashboard yardımcı iş mantığı (forms.py METACLASS riski → ilk turda hariç,
    # başarılı Docker testinden sonra eklenebilir; permissions/context_processors düz)
    "dashboard/permissions.py",
    "dashboard/context_processors.py",
]

# Glob eşleşse bile ASLA derlenmeyecek dosya adları (güvenlik ağı).
NEVER = {"__init__.py", "apps.py", "models.py", "urls.py", "settings.py",
         "wsgi.py", "asgi.py", "celery.py", "manage.py", "tests.py"}


def collect(root: str) -> list[str]:
    files: list[str] = []
    for pattern in COMPILE_GLOBS:
        for path in glob.glob(os.path.join(root, pattern)):
            base = os.path.basename(path)
            if base in NEVER:
                continue
            if os.sep + "migrations" + os.sep in path or os.sep + "management" + os.sep in path:
                continue
            if path not in files:
                files.append(path)
    return sorted(files)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--root", default=os.getcwd())
    args = ap.parse_args()

    files = collect(args.root)
    if not files:
        print("cythonize: derlenecek dosya bulunamadı (root=%s)" % args.root, file=sys.stderr)
        return 1

    rel = [os.path.relpath(f, args.root) for f in files]
    print("cythonize: %d modül derlenecek:" % len(files))
    for r in rel:
        print("  +", r)

    if args.dry_run:
        print("cythonize: --dry-run -> derleme atlandı.")
        return 0

    # Cython inplace derleme (.py -> .c -> .so). gcc + cython gerekir (Docker build).
    # cythonize CLI build_ext'i de yapar (-j paralel).
    cmd = ["cythonize", "--inplace", "-3", "-j", "4", *files]
    print("cythonize: çalıştırılıyor:", " ".join(cmd[:4]), "... (%d dosya)" % len(files))
    subprocess.run(cmd, check=True, cwd=args.root)

    # Kaynağı temizle: .py + üretilen .c sil, yalnız .so kalsın.
    removed = 0
    for f in files:
        c_file = f[:-3] + ".c"
        for victim in (f, c_file):
            if os.path.exists(victim):
                os.remove(victim)
                removed += 1
    print("cythonize: %d modül .so'ya derlendi, %d kaynak dosya silindi." % (len(files), removed))

    # Doğrulama: kritik modülün .py'si gitmiş, .so'su var mı?
    lic_py = os.path.join(args.root, "api", "licensing.py")
    lic_so = glob.glob(os.path.join(args.root, "api", "licensing.*.so"))
    if os.path.exists(lic_py) or not lic_so:
        print("cythonize: UYARI — api/licensing .so doğrulaması başarısız!", file=sys.stderr)
        return 2
    print("cythonize: doğrulama OK — api/licensing derlendi (.py yok, .so var).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
