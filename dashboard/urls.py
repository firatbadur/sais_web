"""Dashboard URL routing.

`sais_web/urls.py` bunu `/dashboard/` prefix'iyle include eder.
"""
from __future__ import annotations

from django.urls import path

from . import api_views, views


app_name = "dashboard"


urlpatterns = [
    # --- Authentication ---
    path("login/", views.DashboardLoginView.as_view(), name="login"),
    path("logout/", views.DashboardLogoutView.as_view(), name="logout"),
    path("forgot-password/", views.ForgotPasswordView.as_view(), name="forgot_password"),

    # --- Home ---
    path("", views.HomeView.as_view(), name="home"),

    # --- Reports ---
    path("reports/readings/", views.ReadingsReportView.as_view(), name="reports_readings"),
    path("reports/aggregates/", views.AggregatesReportView.as_view(), name="reports_aggregates"),
    path("reports/calibrations/", views.CalibrationsReportView.as_view(), name="reports_calibrations"),
    path("reports/power-offs/", views.PowerOffsReportView.as_view(), name="reports_power_offs"),
    path("reports/commands/", views.CommandsReportView.as_view(), name="reports_commands"),
    path("reports/system-logs/", views.SystemLogsReportView.as_view(), name="reports_system_logs"),

    # --- Operator ---
    path("operator/sample-trigger/", views.SampleTriggerView.as_view(), name="operator_sample"),
    path("operator/alarms/", views.AlarmsView.as_view(), name="operator_alarms"),

    # --- Management (readonly) ---
    path("management/stations/", views.StationsOverviewView.as_view(), name="management_stations"),
    path("management/connections/", views.ConnectionsOverviewView.as_view(), name="management_connections"),
    path("management/sensors/", views.SensorsOverviewView.as_view(), name="management_sensors"),

    # --- Admin pages (rol=1) ---
    path("admin-pages/users/", views.UserListView.as_view(), name="admin_users"),
    path("admin-pages/users/create/", views.UserCreateView.as_view(), name="admin_user_create"),
    path("admin-pages/users/<int:pk>/edit/", views.UserUpdateView.as_view(), name="admin_user_edit"),
    path("admin-pages/users/<int:pk>/reset-password/", views.UserResetPasswordView.as_view(),
         name="admin_user_reset_pw"),
    path("admin-pages/api-logs/", views.ApiLogsView.as_view(), name="admin_api_logs"),
    path("admin-pages/system-control/", views.SystemControlView.as_view(),
         name="admin_system_control"),
    path("admin-pages/backups/", views.BackupRestoreView.as_view(), name="admin_backups"),
    path("admin-pages/license/", views.LicenseStatusView.as_view(), name="admin_license"),
    path("admin-pages/web-settings/", views.WebSettingsView.as_view(),
         name="admin_web_settings"),

    # --- License lock screen (menüsüz; middleware buraya yönlendirir) ---
    path("license-expired/", views.LicenseExpiredView.as_view(), name="license_expired"),

    # --- Settings ---
    path("settings/profile/", views.ProfileView.as_view(), name="profile"),
    path("settings/change-password/", views.ChangePasswordView.as_view(), name="change_password"),
    path("settings/preferences/", views.PreferencesView.as_view(), name="preferences"),

    # --- AJAX API (home widgets) ---
    path("api/home/kpis/", api_views.home_kpis, name="api_home_kpis"),
    path("api/home/snapshot/", api_views.home_snapshot, name="api_home_snapshot"),
    path("api/home/trend/", api_views.home_trend, name="api_home_trend"),
    path("api/home/events/", api_views.home_events, name="api_home_events"),

    # --- AJAX API (raporlar) ---
    path("api/parameters/", api_views.station_parameters, name="api_station_parameters"),

    # --- AJAX API (yönetici) ---
    path("api/system-control/status/", api_views.system_control_status,
         name="api_system_control_status"),
    path("api/backups/status/", api_views.backup_status, name="api_backup_status"),
    path("api/backups/<int:pk>/download/", api_views.backup_download, name="api_backup_download"),
]
