from rest_framework import permissions


# Bakanlık (yalnız-API) rolü — bkz. users.models.CustomUser.rol
ROLE_MINISTRY = 4


class MinistryReadOnly(permissions.IsAuthenticated):
    """Authenticated zorunlu + Bakanlık (rol=4) kullanıcısını salt-okunur kısıtlar.

    DEFAULT_PERMISSION_CLASSES olarak kullanılır: per-view `permission_classes`
    override etmeyen tüm endpoint'lerde rol 4 yalnız SAFE_METHODS (GET/HEAD/OPTIONS)
    çağırabilir; diğer roller eski IsAuthenticated davranışında kalır.
    """

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        if getattr(request.user, "rol", None) == ROLE_MINISTRY:
            return request.method in permissions.SAFE_METHODS
        return True


class IsAdminUserOrReadOnly(permissions.IsAuthenticated):

    def has_permission(self, request, view):
        if request.user and request.user.is_authenticated:
            if request.user.is_staff:  # Kullanıcı bir yönetici mi?
                return True  # Yöneticiye her şeyi izin ver
            return request.method in permissions.SAFE_METHODS  # Diğer kullanıcılara sadece "Güvenli" metodları (GET vb.) izin ver

        return False  # Kullanıcı kimlik doğrulamasından geçemediyse izin verme


# class ReadOnly(permissions.IsAuthenticated):
#
#     def has_permission(self, request, view):
#         if request.user and request.user.is_authenticated:
#             if request.user.is_staff:  # Kullanıcı bir yönetici mi?
#                 return True  # Yöneticiye her şeyi izin ver
#             return request.method in permissions.SAFE_METHODS  # Diğer kullanıcılara sadece "Güvenli" metodları (GET vb.) izin ver
#
#         return False  # Kullanıcı kimlik doğrulamasından geçemediyse izin verme

# class ReadOnly(permissions.BasePermission):
#     def has_permission(self, request, view):
#         # Yönetici kullanıcılar için her zaman izin ver
#         if request.user.is_authenticated and request.user.is_staff:
#             return True
#         # Diğer kullanıcılar için sadece "Güvenli" metodları (GET, HEAD, OPTIONS) izin ver
#         return request.method in permissions.SAFE_METHODS