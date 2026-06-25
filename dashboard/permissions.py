"""Rol-bazlı erişim kontrolü.

CustomUser.rol değerleri:
  1 → Sistem Yöneticisi (admin)
  2 → Operatör
  3 → Normal Kullanıcı
  4 → Bakanlık Kullanıcısı (yalnız-API; panele giriş yapamaz)
"""
from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied


ROLE_ADMIN = 1
ROLE_OPERATOR = 2
ROLE_USER = 3
ROLE_MINISTRY = 4

ROLE_NAMES = {
    ROLE_ADMIN: "admin",
    ROLE_OPERATOR: "operator",
    ROLE_USER: "user",
    ROLE_MINISTRY: "ministry",
}


class RoleRequiredMixin(LoginRequiredMixin):
    """View base mixin — izin verilen rollerde değilse 403."""

    required_roles: tuple[int, ...] = (ROLE_ADMIN, ROLE_OPERATOR, ROLE_USER)

    def dispatch(self, request, *args, **kwargs):
        user = request.user
        if user.is_authenticated:
            user_role = getattr(user, "rol", None)
            # Superuser (is_superuser) her zaman geçer — kurtarıcı mekanizma
            if not user.is_superuser and user_role not in self.required_roles:
                raise PermissionDenied(
                    f"Bu sayfaya erişim için gerekli rol yok "
                    f"(mevcut={user_role}, gerekli={self.required_roles})."
                )
        return super().dispatch(request, *args, **kwargs)


class AdminRequiredMixin(RoleRequiredMixin):
    required_roles = (ROLE_ADMIN,)


class OperatorRequiredMixin(RoleRequiredMixin):
    required_roles = (ROLE_ADMIN, ROLE_OPERATOR)


# Helper — template veya view'da direkt rol kontrolü için
def user_has_role(user, *roles: int) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    return getattr(user, "rol", None) in roles


def can_manage_target_user(acting_user, target) -> bool:
    """`acting_user`, `target` kullanıcısını düzenleyebilir/şifresini sıfırlayabilir mi?

    Kural: **Sistem Yöneticisi'nin (rol=1 / superuser) eklediği** kullanıcılara
    operatör (rol=2) müdahale edemez; ayrıca hedef kullanıcının kendisi admin ise
    de korunur. Admin/superuser her kullanıcıyı yönetir.

    "Ekleyen" bilgisi ``CustomUser.added_by`` (oluşturanın id'si) üzerinden
    çözülür; legacy kayıtlarda default 1 (ilk admin) olduğundan onlar da korunur.
    """
    # Admin/superuser her zaman yönetebilir.
    if user_has_role(acting_user, ROLE_ADMIN):
        return True

    # Hedefin kendisi admin/superuser ise operatör dokunamaz.
    if getattr(target, "is_superuser", False) or getattr(target, "rol", None) == ROLE_ADMIN:
        return False

    # Hedefi ekleyen kişi admin/superuser ise operatör dokunamaz.
    creator = None
    added_by = getattr(target, "added_by", None)
    if added_by:
        from django.contrib.auth import get_user_model
        creator = get_user_model().objects.filter(pk=added_by).first()
    if creator is not None and (
        getattr(creator, "is_superuser", False)
        or getattr(creator, "rol", None) == ROLE_ADMIN
    ):
        return False

    return True


def can_view_admin_events(user) -> bool:
    """Sistem yöneticisinin (rol=1 / superuser) hareketlerini görme yetkisi.

    Yalnızca admin/superuser, admin aktörlü olayları (SystemLog) görebilir;
    operatör ve normal kullanıcı bu olayları ne raporlarda ne dashboard'da görür.
    """
    if getattr(user, "is_superuser", False):
        return True
    return getattr(user, "rol", None) == ROLE_ADMIN
