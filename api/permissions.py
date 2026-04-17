from rest_framework import permissions


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