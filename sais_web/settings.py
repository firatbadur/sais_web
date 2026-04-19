"""
Django settings for sais_web project.

Yapılandırma `.env` dosyası üzerinden okunur (python-dotenv).
Örnek için `.env.example` dosyasına bakın.
"""
import os
from pathlib import Path

from django.contrib.messages import constants as messages
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent

# .env dosyasını oku (varsa)
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in ("1", "true", "yes", "on")


def env_list(name: str, default: str = "") -> list[str]:
    value = os.getenv(name, default)
    return [item.strip() for item in value.split(",") if item.strip()]


# SECURITY
SECRET_KEY = os.getenv(
    "DJANGO_SECRET_KEY",
    "django-insecure-dev-only-change-me-in-production",
)

DEBUG = env_bool("DJANGO_DEBUG", default=True)

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", "")


# Application definition
INSTALLED_APPS = [
    "jazzmin",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "jsonify",
    "django.contrib.humanize",
    "users",
    "rest_framework",
    "rest_framework.authtoken",
    "dj_rest_auth",
    "api",
    "sais_domain",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django_session_timeout.middleware.SessionTimeoutMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
if DEBUG:
    MIDDLEWARE.append("debug_toolbar.middleware.DebugToolbarMiddleware")

ROOT_URLCONF = "sais_web.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "sais_web.wsgi.application"
ASGI_APPLICATION = "sais_web.asgi.application"


# Database — Microsoft SQL Server (mssql-django)
_mssql_options = {
    "driver": os.getenv("MSSQL_DRIVER", "ODBC Driver 17 for SQL Server"),
}
if env_bool("MSSQL_TRUSTED_CONNECTION", default=False):
    _mssql_options["trusted_connection"] = "yes"
if env_bool("MSSQL_TRUST_SERVER_CERTIFICATE", default=True):
    _mssql_options["extra_params"] = "TrustServerCertificate=yes"

DATABASES = {
    "default": {
        "ENGINE": "mssql",
        "NAME": os.getenv("MSSQL_DB", "envisoft"),
        "USER": os.getenv("MSSQL_USER", "sa"),
        "PASSWORD": os.getenv("MSSQL_PASSWORD", ""),
        "HOST": os.getenv("MSSQL_HOST", "localhost"),
        "PORT": os.getenv("MSSQL_PORT", "1433"),
        "CONN_MAX_AGE": int(os.getenv("DB_CONN_MAX_AGE", "60")),
        "OPTIONS": _mssql_options,
    }
}


# Cache (Redis)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/1")
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        },
    }
}


AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


LANGUAGE_CODE = os.getenv("DJANGO_LANGUAGE_CODE", "tr")
TIME_ZONE = os.getenv("DJANGO_TIME_ZONE", "Europe/Istanbul")
USE_I18N = True
USE_TZ = env_bool("DJANGO_USE_TZ", default=False)


# Static & media files
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles_root"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
        if not DEBUG
        else "whitenoise.storage.CompressedStaticFilesStorage",
    },
}


DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

MESSAGE_TAGS = {
    messages.DEBUG: "alert-info",
    messages.INFO: "alert-info",
    messages.SUCCESS: "alert-success",
    messages.WARNING: "alert-warning",
    messages.ERROR: "alert-danger",
}

AUTH_USER_MODEL = "users.CustomUser"

AUTHENTICATION_BACKENDS = ("django.contrib.auth.backends.ModelBackend",)


# Sessions
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_COOKIE_AGE = int(os.getenv("SESSION_COOKIE_AGE", str(60 * 600)))
SESSION_TIMEOUT_REDIRECT = "logout/"


# REST framework
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework.authentication.BasicAuthentication",
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.TokenAuthentication",
    ),
    "DEFAULT_PARSER_CLASSES": (
        "rest_framework.parsers.MultiPartParser",
        "rest_framework.parsers.FormParser",
        "rest_framework.parsers.JSONParser",
    ),
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 100,
}


APPEND_SLASH = False

INTERNAL_IPS = ["127.0.0.1"]


DEBUG_TOOLBAR_PANELS = [
    "debug_toolbar.panels.versions.VersionsPanel",
    "debug_toolbar.panels.timer.TimerPanel",
    "debug_toolbar.panels.settings.SettingsPanel",
    "debug_toolbar.panels.headers.HeadersPanel",
    "debug_toolbar.panels.request.RequestPanel",
    "debug_toolbar.panels.sql.SQLPanel",
    "debug_toolbar.panels.staticfiles.StaticFilesPanel",
    "debug_toolbar.panels.templates.TemplatesPanel",
    "debug_toolbar.panels.cache.CachePanel",
    "debug_toolbar.panels.signals.SignalsPanel",
    "debug_toolbar.panels.logging.LoggingPanel",
    "debug_toolbar.panels.redirects.RedirectsPanel",
]

DEBUG_TOOLBAR_CONFIG = {
    "SHOW_TOOLBAR_CALLBACK": lambda r: False,
}


# Production security
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# Admin popup'ları (change related) SAMEORIGIN iframe'e izin vermeli.
X_FRAME_OPTIONS = "SAMEORIGIN"
if not DEBUG:
    SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", default=False)


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{asctime}] {levelname} {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": os.getenv("DJANGO_LOG_LEVEL", "INFO"),
    },
}


JAZZMIN_SETTINGS = {
    # Branding
    "site_title": "SAIS Yönetim",
    "site_header": "SAIS Yönetim Paneli",
    "site_brand": "SAIS",
    "site_icon": "images/logo/icon.png",
    "site_logo": "images/logo/icon.png",
    "login_logo": "images/logo/logo-v1.png",
    "site_logo_classes": "img-circle",
    "welcome_sign": "SAIS Web SCADA — Yönetim Paneli",
    "copyright": "Envisoft",
    "user_avatar": None,

    # Top menu
    "topmenu_links": [
        {"name": "Panel", "url": "admin:index", "permissions": ["auth.view_user"]},
        {"app": "api"},
        {"app": "users"},
    ],

    # Side menu
    "show_sidebar": True,
    "navigation_expanded": True,
    "hide_apps": [],
    "hide_models": [],
    "order_with_respect_to": [
        "api",
        "api.Station",
        "api.StationType",
        "api.Connection",
        "api.Parameter",
        "api.Sensor",
        "api.SensorLatest",
        "api.Reading",
        "api.Calibration",
        "api.Command",
        "api.RequestType",
        "api.PowerOff",
        "api.StatusCode",
        "api.LogType",
        "api.SystemLog",
        "api.ApiLog",
        "sais_domain",
        "sais_domain.SaisCabinet",
        "sais_domain.EnvisoftChannel",
        "users",
        "users.CustomUser",
        "auth",
        "authtoken",
    ],

    # Icons (FontAwesome 6 free)
    "icons": {
        # auth
        "auth": "fas fa-shield-alt",
        "auth.Group": "fas fa-users-cog",
        "authtoken": "fas fa-key",
        "authtoken.TokenProxy": "fas fa-key",

        # users
        "users": "fas fa-user-friends",
        "users.CustomUser": "fas fa-user",

        # api - istasyon / bağlantı
        "api": "fas fa-industry",
        "api.Station": "fas fa-building",
        "api.StationType": "fas fa-tag",
        "api.Connection": "fas fa-network-wired",

        # api - parametre / sensör
        "api.Parameter": "fas fa-sliders-h",
        "api.Sensor": "fas fa-microchip",
        "api.SensorLatest": "fas fa-tachometer-alt",

        # api - veri
        "api.Reading": "fas fa-chart-line",
        "api.Calibration": "fas fa-balance-scale",
        "api.PowerOff": "fas fa-power-off",
        "api.Command": "fas fa-terminal",
        "api.RequestType": "fas fa-bell",

        # api - meta / log
        "api.StatusCode": "fas fa-list-alt",
        "api.LogType": "fas fa-tags",
        "api.SystemLog": "fas fa-clipboard-list",
        "api.ApiLog": "fas fa-exchange-alt",

        # sais_domain
        "sais_domain": "fas fa-water",
        "sais_domain.SaisCabinet": "fas fa-sim-card",
        "sais_domain.EnvisoftChannel": "fas fa-link",

        # admin
        "admin.LogEntry": "fas fa-file-alt",
    },
    "default_icon_parents": "fas fa-chevron-circle-right",
    "default_icon_children": "fas fa-circle",

    # UI behavior
    # Jazzmin 3.x modal iframe popup'ı bozuk (related field edit bozuk görünüyor);
    # native Django popup (yeni pencere) daha stabil.
    "related_modal_active": False,
    "custom_js": None,
    "show_ui_builder": False,
    "changeform_format": "horizontal_tabs",
    "changeform_format_overrides": {
        "auth.user": "collapsible",
        "auth.group": "vertical_tabs",
        "users.customuser": "collapsible",
    },

    # Search bar
    "search_model": ["api.Station", "api.StationType", "api.Parameter", "api.Sensor", "users.CustomUser"],

    "custom_css": "css/admin-panel-dark.css",

    # Language switcher disable
    "language_chooser": False,
}

JAZZMIN_UI_TWEAKS = {
    "navbar_small_text": False,
    "footer_small_text": False,
    "body_small_text": False,
    "brand_small_text": False,
    "brand_colour": "navbar-primary",
    "accent": "accent-teal",
    "navbar": "navbar-dark navbar-primary",
    "no_navbar_border": True,
    "navbar_fixed": True,
    "layout_boxed": False,
    "footer_fixed": False,
    "sidebar_fixed": True,
    "sidebar": "sidebar-dark-primary",
    "sidebar_nav_small_text": False,
    "sidebar_disable_expand": False,
    "sidebar_nav_child_indent": True,
    "sidebar_nav_compact_style": False,
    "sidebar_nav_legacy_style": False,
    "sidebar_nav_flat_style": False,
    "theme": "flatly",
    # Jazzmin 3.x: dark_mode_theme kaldırıldı, yerine default_theme_mode.
    "default_theme_mode": "auto",
    "button_classes": {
        "primary": "btn-primary",
        "secondary": "btn-secondary",
        "info": "btn-info",
        "warning": "btn-warning",
        "danger": "btn-danger",
        "success": "btn-success",
    },
    "actions_sticky_top": True,
}
