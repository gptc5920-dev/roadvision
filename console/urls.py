from django.urls import path
from django.views.generic import RedirectView

from . import views


urlpatterns = [
    path("", views.landing, name="home"),
    path("healthz/", views.health, name="health"),
    path("livez/", views.liveness, name="liveness"),
    path("auth/", views.auth_page, name="auth"),
    path("logout/", views.sign_out, name="logout"),
    path("_authenticated/", views.authenticated_index, name="authenticated_index"),
    path("_authenticated/admin/", views.video_visualizer, name="admin_video_analyzer"),
    path("_authenticated/admin/video-analyzer/", views.video_visualizer, name="admin_video_analyzer_explicit"),
    path("_authenticated/admin/video-analyzer/<int:analysis_id>/status/", views.video_visualizer_status, name="admin_video_visualizer_status"),
    path("_authenticated/admin/video-analyzer/<int:analysis_id>/csv/", views.video_analysis_csv, name="video_analysis_csv"),
    path("_authenticated/admin/video-analyzer/clear/", views.clear_video_analyses, name="clear_video_analyses"),
    path(
        "_authenticated/admin/video-visualizer/",
        RedirectView.as_view(pattern_name="admin_video_analyzer_explicit", permanent=False, query_string=True),
        name="admin_video_visualizer",
    ),
    path("_authenticated/admin/video-visualizer/<int:analysis_id>/report/", views.video_visualizer_report, name="admin_video_visualizer_report"),
    path("_authenticated/admin/training-dataset/", views.training_dataset_module, name="admin_training_dataset"),
    path("_authenticated/admin/training-dataset/export/", views.yolo_dataset_export, name="admin_training_dataset_export"),
    path("_authenticated/admin/overview/", views.module_placeholder, {"module": "overview"}, name="admin_overview"),
    path("_authenticated/admin/live-map/", views.module_placeholder, {"module": "live-map"}, name="admin_live_map"),
    path("_authenticated/admin/detections/", views.module_placeholder, {"module": "detections"}, name="admin_detections"),
    path("_authenticated/admin/dispatch/", views.module_placeholder, {"module": "dispatch"}, name="admin_dispatch"),
    path("_authenticated/admin/fleet-cams/", views.module_placeholder, {"module": "fleet-cams"}, name="admin_fleet_cams"),
    path("_authenticated/admin/detection-sources/", views.module_placeholder, {"module": "detection-sources"}, name="admin_detection_sources"),
    path("_authenticated/admin/personnel/", views.module_placeholder, {"module": "personnel"}, name="admin_personnel"),
    path("_authenticated/admin/settings/", views.module_placeholder, {"module": "settings"}, name="admin_settings"),
    path("<path:unused>/", views.not_found_redirect, name="not_found_redirect"),
]
