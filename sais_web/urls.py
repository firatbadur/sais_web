from django.urls import re_path as url
from django.conf.urls import include,handler404,handler403,handler500,handler400
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path,re_path
from .views import home
from django.conf import settings
from django.views.static import serve
from api import views as api_views

urlpatterns = [
                  path('admin/', admin.site.urls),
                  path('', home, name="home"),

                  path('__debug__/', include('debug_toolbar.urls')),
                  path('api/', include('api.urls')),
                  path('api-auth/',include('rest_framework.urls')),
                  path('api/rest-auth/', include('dj_rest_auth.urls')),

                  # SIM SERVİSLER .................#

                  path('GetServerDatetime',api_views.GetServerDatetimeView.as_view()),
                  path('GetData',api_views.GetReadsDataView.as_view()),
                  path('GetInstantData',api_views.GetLatestReadsView.as_view()),
                  path('GetLastDataDate',api_views.GetLastReadTimeView.as_view()),
                  path('GetChannelInformation',api_views.GetChannelInfoView.as_view()),
                  path('GetStationInformation',api_views.GetStationInformationView.as_view()),
                  path('GetCalibration',api_views.GetCalibrationView.as_view()),
                  path('GetPowerOffTimes',api_views.GetPoweroffView.as_view()),
                  path('GetLog',api_views.GetLogView.as_view()),
                  path('StartSample',api_views.StartSampleView.as_view()),
                  # SIM SERVİSLER .................#


                  re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
                  re_path(r'^static/(?P<path>.*)$', serve, {'document_root': settings.STATIC_ROOT}),


              ]+ static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# tasks.start()