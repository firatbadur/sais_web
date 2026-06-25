"""SAIS kabin → Bakanlık kullanıcısı otomatik senkronizasyonu.

Bir ``SaisCabinet`` kaydedildiğinde/güncellendiğinde, kabinin Bakanlık erişim
bilgileriyle (``auth_username`` / ``auth_secret``) eşleşen bir **Bakanlık
Kullanıcısı** (``CustomUser.rol == 4``) otomatik açılır/güncellenir. Bu hesap
Bakanlık'ın bizim *inbound* API'mize basic-auth ile bağlanması içindir (panele
giriş yapamaz; bkz. ``DashboardLoginForm.confirm_login_allowed``).

Hesap kabine ``cabinet.user`` FK'siyle bağlanır → kullanıcı adı sonradan
değişirse (örn. SendHostChanged) aynı hesap yeniden adlandırılır, yeni hesap
açılmaz. E-posta/telefon zorunlu olabildiğinden boşsa fake değer atanır.
"""
from __future__ import annotations

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import SaisCabinet

logger = logging.getLogger("sais_domain.signals")

# users.permissions yerine sabit — app yükleme sırası bağımlılığını azaltır.
ROLE_MINISTRY = 4


def sync_cabinet_ministry_user(cabinet: SaisCabinet):
    """Kabinin erişim bilgileriyle eşleşen Bakanlık kullanıcısını açar/günceller.

    Asla exception fırlatmaz — kabin kaydı kullanıcı senkronu yüzünden
    başarısız olmamalı; hata durumunda yalnız log düşer.
    """
    try:
        from django.contrib.auth import get_user_model
        User = get_user_model()

        username = (cabinet.auth_username or "").strip()
        if not username:
            return None  # kullanıcı adı olmadan hesap açılamaz
        secret = cabinet.auth_secret or ""

        managed = None
        # 1) Önce kabine bağlı hesabı dene — ama bir panel admin'ini (rol=1) yanlışlıkla
        #    Bakanlık hesabına çevirmemek için yalnız zaten Bakanlık hesabı ya da
        #    aynı kullanıcı adına sahip kayıt "yönetilen" sayılır.
        if cabinet.user_id:
            candidate = User.objects.filter(pk=cabinet.user_id).first()
            if candidate and (
                getattr(candidate, "rol", None) == ROLE_MINISTRY
                or candidate.username == username
            ):
                managed = candidate

        # 2) Kabine bağlı yönetilen hesap yoksa, kullanıcı adından bul.
        if managed is None:
            existing = User.objects.filter(username=username).first()
            if existing is not None:
                if getattr(existing, "rol", None) == ROLE_MINISTRY:
                    managed = existing
                else:
                    # Aynı kullanıcı adına sahip bir panel hesabı var → ele geçirme,
                    # otomatik Bakanlık hesabı oluşturmayı atla (çakışma).
                    logger.warning(
                        "Kabin %s için '%s' kullanıcı adı panel hesabıyla çakışıyor; "
                        "otomatik Bakanlık hesabı atlandı.", cabinet.pk, username,
                    )
                    return None
            else:
                managed = User(username=username)

        # 3) Yeni kullanıcı adı başka bir hesapta kullanılıyorsa yeniden adlandırma yapma.
        clash = User.objects.filter(username=username).exclude(pk=managed.pk or 0).exists()
        if clash:
            logger.warning(
                "Kabin %s için '%s' kullanıcı adı başka hesapta; senkron atlandı.",
                cabinet.pk, username,
            )
            return None

        managed.username = username
        managed.rol = ROLE_MINISTRY
        managed.is_active = True
        if not (managed.email or "").strip():
            managed.email = f"{username}@sais.local"        # fake (zorunlu olabilir)
        if not (getattr(managed, "phone_number", "") or "").strip():
            managed.phone_number = "0000000000"             # fake
        if secret:
            managed.set_password(secret)
        elif not managed.pk:
            managed.set_unusable_password()
        managed.save()

        # Bakanlık hesabı inbound API'ye basic-auth + DRF token ile erişir.
        try:
            from rest_framework.authtoken.models import Token
            Token.objects.get_or_create(user=managed)
        except Exception:  # noqa: BLE001 — token opsiyonel
            pass

        # Kabini hesaba bağla — post_save tetiklememek için update() kullan.
        if cabinet.user_id != managed.pk:
            SaisCabinet.objects.filter(pk=cabinet.pk).update(user=managed)

        return managed
    except Exception as exc:  # noqa: BLE001 — kabin kaydını asla kırma
        logger.exception("Kabin %s Bakanlık kullanıcı senkronu başarısız: %s",
                         getattr(cabinet, "pk", "?"), exc)
        return None


@receiver(post_save, sender=SaisCabinet, dispatch_uid="saiscabinet_ministry_user_sync")
def _saiscabinet_post_save(sender, instance, **kwargs):
    # Fixture/loaddata (raw) sırasında kullanıcı türetme — şema henüz tam
    # tutarlı olmayabilir; config import bunu atlasın.
    if kwargs.get("raw"):
        return
    sync_cabinet_ministry_user(instance)
