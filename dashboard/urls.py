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
    path("reports/alarms/", views.AlarmReportsView.as_view(), name="reports_alarms"),

    # --- Operator ---
    path("operator/sample-trigger/", views.ScenarioBuilderView.as_view(), name="operator_sample"),
    path("operator/scenario-designer/", views.ScenarioDesignerView.as_view(),
         name="operator_scenario_designer"),
    path("operator/calibration/", views.CalibrationWizardView.as_view(),
         name="operator_calibration"),
    path("operator/alarms/", views.AlarmsView.as_view(), name="operator_alarms"),

    # --- Management (readonly) ---
    path("management/stations/", views.StationsOverviewView.as_view(), name="management_stations"),
    path("management/connections/", views.ConnectionsOverviewView.as_view(), name="management_connections"),
    path("management/sensors/", views.SensorsOverviewView.as_view(), name="management_sensors"),

    # --- Takvim Hatırlatıcı ---
    path("reminders/", views.RemindersView.as_view(), name="reminders"),

    # --- Documents (doküman yönetimi) ---
    path("documents/", views.DocumentListView.as_view(), name="documents"),
    path("documents/upload/", views.DocumentUploadView.as_view(), name="document_upload"),
    path("documents/<int:pk>/download/", views.DocumentDownloadView.as_view(),
         name="document_download"),
    path("documents/<int:pk>/delete/", views.DocumentDeleteView.as_view(),
         name="document_delete"),

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
    path("admin-pages/notifications/", views.NotificationCenterView.as_view(),
         name="admin_notifications"),

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
    path("api/home/digital-command/", api_views.digital_output_command,
         name="api_digital_command"),
    path("api/sensors/reorder/", api_views.sensors_reorder, name="api_sensors_reorder"),

    # --- AJAX API (raporlar) ---
    path("api/parameters/", api_views.station_parameters, name="api_station_parameters"),

    # --- AJAX API (Takvim Hatırlatıcı) ---
    path("api/reminders/list/", api_views.reminders_list, name="api_reminders_list"),
    path("api/reminders/feed/", api_views.reminders_feed, name="api_reminders_feed"),
    path("api/reminders/save/", api_views.reminder_save, name="api_reminder_save"),
    path("api/reminders/done/", api_views.reminder_done, name="api_reminder_done"),
    path("api/reminders/delete/", api_views.reminder_delete, name="api_reminder_delete"),

    # --- AJAX API (yönetici) ---
    path("api/system-control/status/", api_views.system_control_status,
         name="api_system_control_status"),
    path("api/backups/status/", api_views.backup_status, name="api_backup_status"),
    path("api/backups/<int:pk>/download/", api_views.backup_download, name="api_backup_download"),
    path("api/version/", api_views.version_info, name="api_version_info"),
    path("api/version/update/", api_views.trigger_update, name="api_trigger_update"),
    path("api/port-check/", api_views.port_check, name="api_port_check"),
    path("api/server-info/", api_views.server_info, name="api_server_info"),

    # --- AJAX API (Bildirim Merkezi) ---
    path("api/notifications/recipients/", api_views.notification_recipients,
         name="api_notification_recipients"),
    path("api/notifications/send-test/", api_views.notification_send_test,
         name="api_notification_send_test"),
    path("api/notifications/templates/", api_views.notification_templates,
         name="api_notification_templates"),

    # --- AJAX API (Alarm Yönetimi) ---
    path("api/alarms/io/", api_views.alarm_io, name="api_alarm_io"),
    path("api/alarms/rules/", api_views.alarm_rules, name="api_alarm_rules"),

    # --- AJAX API (Numune Senaryosu) ---
    path("api/scenario/list/", api_views.scenario_list, name="api_scenario_list"),
    path("api/scenario/detail/", api_views.scenario_detail, name="api_scenario_detail"),
    path("api/scenario/save/", api_views.scenario_save, name="api_scenario_save"),
    path("api/scenario/delete/", api_views.scenario_delete, name="api_scenario_delete"),
    path("api/scenario/activate/", api_views.scenario_activate, name="api_scenario_activate"),
    path("api/scenario/status/", api_views.scenario_status, name="api_scenario_status"),
    path("api/scenario/history/", api_views.scenario_history, name="api_scenario_history"),
    path("api/scenario/ministry/", api_views.scenario_request_ministry, name="api_scenario_ministry"),
    path("api/scenario/cancel-run/", api_views.scenario_cancel_run, name="api_scenario_cancel_run"),

    # --- AJAX API (İnteraktif Kalibrasyon) ---
    path("api/calibration/params/", api_views.calibration_params, name="api_calibration_params"),
    path("api/calibration/live/", api_views.calibration_live, name="api_calibration_live"),
    path("api/calibration/save/", api_views.calibration_save, name="api_calibration_save"),
    path("api/calibration/send-sim/", api_views.calibration_send_sim, name="api_calibration_send_sim"),

    # --- AJAX API (Senaryo Tasarımcı / node-graph) ---
    path("api/scenario-graph/list/", api_views.graph_list, name="api_graph_list"),
    path("api/scenario-graph/get/", api_views.graph_get, name="api_graph_get"),
    path("api/scenario-graph/save/", api_views.graph_save, name="api_graph_save"),
    path("api/scenario-graph/delete/", api_views.graph_delete, name="api_graph_delete"),
    path("api/scenario/digital-sensors/", api_views.scenario_digital_sensors,
         name="api_scenario_digital_sensors"),
]
