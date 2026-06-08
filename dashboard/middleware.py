"""
Lisans kilidi middleware'i.

Lisans aktif değilse (`api.licensing.license_active()` False), `/dashboard/`
altındaki tüm istekleri "lisans doldu" ekranına yönlendirir — tam kilit.

Muaf tutulanlar (kullanıcı kilidi açabilsin / çıkış yapabilsin diye):
  - login / logout / forgot-password
  - license-expired ekranının kendisi (sonsuz redirect olmasın)
  - /dashboard/api/license/ (yenile/uygula endpoint'leri)

`/dashboard/` dışındaki yollar (örn. Django `/admin/`, statik, REST API) bu
middleware tarafından engellenmez — superuser kurtarma için `/admin/` açık kalır.
Asıl "iş yapma" durdurma Celery task gate'lerinde (license_active) yapılır.
"""
from __future__ import annotations

from django.shortcuts import redirect


class LicenseLockMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self._exempt = None

    def _exempt_prefixes(self) -> tuple[str, ...]:
        if self._exempt is None:
            from django.urls import reverse
            prefixes = ["/dashboard/api/license/"]
            for name in (
                "dashboard:login", "dashboard:logout",
                "dashboard:forgot_password", "dashboard:license_expired",
            ):
                try:
                    prefixes.append(reverse(name))
                except Exception:  # noqa: BLE001 — URL henüz çözülemiyorsa atla
                    pass
            self._exempt = tuple(prefixes)
        return self._exempt

    def __call__(self, request):
        path = request.path
        if path.startswith("/dashboard/") and not path.startswith(self._exempt_prefixes()):
            from api.licensing import license_active
            if not license_active():
                return redirect("dashboard:license_expired")
        return self.get_response(request)
