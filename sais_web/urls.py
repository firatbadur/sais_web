from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.shortcuts import redirect
from django.urls import include, path, re_path
from django.views.static import serve

from api import views as api_views


def _root_redirect(request):
    """`/` → login'e veya dashboard home'a yönlendir (LoginRequiredMixin karar verir)."""
    return redirect("dashboard:home")


urlpatterns = [
    path("admin/", admin.site.urls),

    # Root → dashboard (auth yoksa login'e, varsa home)
    path("", _root_redirect, name="home"),

    # Dashboard (frontend)
    path("dashboard/", include("dashboard.urls", namespace="dashboard")),

    # i18n — dil değiştirme endpoint'i
    path("i18n/", include("django.conf.urls.i18n")),

    # Debug toolbar (DEBUG mode'da)
    path("__debug__/", include("debug_toolbar.urls")),

    # API
    path("api/", include("api.urls")),
    path("api-auth/", include("rest_framework.urls")),
    path("api/rest-auth/", include("dj_rest_auth.urls")),

    # SAIS/SIM legacy servisleri
    path("GetServerDatetime", api_views.GetServerDatetimeView.as_view()),
    path("GetData", api_views.GetReadsDataView.as_view()),
    path("GetInstantData", api_views.GetLatestReadsView.as_view()),
    path("GetLastDataDate", api_views.GetLastReadTimeView.as_view()),
    path("GetChannelInformation", api_views.GetChannelInfoView.as_view()),
    path("GetStationInformation", api_views.GetStationInformationView.as_view()),
    path("GetCalibration", api_views.GetCalibrationView.as_view()),
    path("GetPowerOffTimes", api_views.GetPoweroffView.as_view()),
    path("GetLog", api_views.GetLogView.as_view()),
    path("StartSample", api_views.StartSampleView.as_view()),

    # Static/media serve
    re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT}),
    re_path(r"^static/(?P<path>.*)$", serve, {"document_root": settings.STATIC_ROOT}),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
