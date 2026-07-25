from django.contrib import admin

from .models import (
    AnalyzerConfiguration,
    DatasetAuditLog,
    DatasetAugmentationJob,
    DatasetImage,
    DatasetVersion,
    DetectionEvent,
    DetectionTest,
    FleetDevice,
    PotholeAnnotation,
    PotholeReport,
    Profile,
    TrainingSession,
    UserRole,
    VideoAnalysis,
    VideoDatasetSample,
    VideoPotholeTrack,
    VideoVisualizerAnalysis,
)


admin.site.register(Profile)
admin.site.register(AnalyzerConfiguration)
admin.site.register(UserRole)
admin.site.register(PotholeReport)
admin.site.register(FleetDevice)
admin.site.register(VideoDatasetSample)
admin.site.register(VideoAnalysis)
admin.site.register(DetectionEvent)
admin.site.register(DatasetVersion)
admin.site.register(DatasetImage)
admin.site.register(PotholeAnnotation)
admin.site.register(DatasetAugmentationJob)
admin.site.register(TrainingSession)
admin.site.register(DetectionTest)
admin.site.register(DatasetAuditLog)
admin.site.register(VideoVisualizerAnalysis)
admin.site.register(VideoPotholeTrack)
