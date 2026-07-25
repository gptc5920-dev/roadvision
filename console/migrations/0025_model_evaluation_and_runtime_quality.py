import re

from django.db import migrations, models


def seed_quality_metadata(apps, schema_editor):
    DatasetImage = apps.get_model("console", "DatasetImage")
    TrainingSession = apps.get_model("console", "TrainingSession")
    AnalyzerConfiguration = apps.get_model("console", "AnalyzerConfiguration")

    for image in DatasetImage.objects.filter(source_group="").iterator():
        filename = image.original_filename or ""
        match = re.search(r"(?:analysis|video)-(\d+)", filename, re.IGNORECASE)
        image.source_group = f"video-analysis-{match.group(1)}" if match else f"image-{image.pk}"
        image.save(update_fields=["source_group"])

    for session in TrainingSession.objects.filter(model_task="").iterator():
        session.model_task = "segment" if "-seg" in (session.model_name or "").lower() else "detect"
        session.save(update_fields=["model_task"])

    invalid_active_ids = list(
        TrainingSession.objects.filter(is_active_video_model=True).exclude(model_task="segment").values_list("pk", flat=True)
    )
    if invalid_active_ids:
        TrainingSession.objects.filter(pk__in=invalid_active_ids).update(is_active_video_model=False)
        AnalyzerConfiguration.objects.filter(model_session_id__in=invalid_active_ids).update(model_session_id=None)

    for image in DatasetImage.objects.filter(
        status="approved",
        review_notes__startswith="Automatically labeled by model session",
    ).iterator():
        image.status = "full"
        image.review_notes = (image.review_notes + " Manual mask review is required before approval.")[:4000]
        image.save(update_fields=["status", "review_notes"])

    AnalyzerConfiguration.objects.filter(confidence_threshold=50).update(confidence_threshold=30)
    AnalyzerConfiguration.objects.filter(input_resolution=640).update(input_resolution=512)


def restore_auto_approved_status(apps, schema_editor):
    DatasetImage = apps.get_model("console", "DatasetImage")
    DatasetImage.objects.filter(
        status="full",
        review_notes__startswith="Automatically labeled by model session",
        review_notes__endswith="Manual mask review is required before approval.",
    ).update(status="approved")


class Migration(migrations.Migration):
    dependencies = [("console", "0024_fleetdevice_stream_fields")]

    operations = [
        migrations.AddField(
            model_name="datasetimage",
            name="source_group",
            field=models.CharField(blank=True, db_index=True, help_text="Stable route, survey, or source-video identifier used to prevent train/test leakage.", max_length=120),
        ),
        migrations.AddField(model_name="trainingsession", name="model_task", field=models.CharField(blank=True, max_length=20)),
        migrations.AddField(model_name="trainingsession", name="local_evaluation_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="trainingsession", name="local_test_images", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="trainingsession", name="local_precision", field=models.DecimalField(blank=True, decimal_places=4, max_digits=7, null=True)),
        migrations.AddField(model_name="trainingsession", name="local_recall", field=models.DecimalField(blank=True, decimal_places=4, max_digits=7, null=True)),
        migrations.AddField(model_name="trainingsession", name="local_map50", field=models.DecimalField(blank=True, decimal_places=4, max_digits=7, null=True)),
        migrations.AddField(model_name="trainingsession", name="local_map5095", field=models.DecimalField(blank=True, decimal_places=4, max_digits=7, null=True)),
        migrations.AddField(model_name="trainingsession", name="local_metrics", field=models.JSONField(blank=True, default=dict)),
        migrations.AlterField(model_name="analyzerconfiguration", name="confidence_threshold", field=models.PositiveSmallIntegerField(default=30)),
        migrations.AlterField(model_name="analyzerconfiguration", name="input_resolution", field=models.PositiveIntegerField(default=512)),
        migrations.AddField(model_name="analyzerconfiguration", name="include_road_damage", field=models.BooleanField(default=True)),
        migrations.AddField(model_name="analyzerconfiguration", name="min_track_appearances", field=models.PositiveSmallIntegerField(default=2)),
        migrations.AddField(model_name="analyzerconfiguration", name="dedup_iou_threshold", field=models.DecimalField(decimal_places=3, default=0.35, max_digits=4)),
        migrations.AddField(model_name="analyzerconfiguration", name="dedup_max_gap_frames", field=models.PositiveIntegerField(default=90)),
        migrations.AddField(model_name="videovisualizeranalysis", name="include_road_damage", field=models.BooleanField(default=True)),
        migrations.AddField(model_name="videovisualizeranalysis", name="min_track_appearances", field=models.PositiveSmallIntegerField(default=2)),
        migrations.AddField(model_name="videovisualizeranalysis", name="dedup_iou_threshold", field=models.DecimalField(decimal_places=3, default=0.35, max_digits=4)),
        migrations.AddField(model_name="videovisualizeranalysis", name="dedup_max_gap_frames", field=models.PositiveIntegerField(default=90)),
        migrations.AddField(model_name="videovisualizeranalysis", name="source_processing_fps", field=models.DecimalField(decimal_places=3, default=0, max_digits=8)),
        migrations.AddField(model_name="videovisualizeranalysis", name="realtime_factor", field=models.DecimalField(decimal_places=3, default=0, max_digits=8)),
        migrations.AddField(model_name="videovisualizeranalysis", name="effective_frame_skip", field=models.PositiveSmallIntegerField(default=1)),
        migrations.AddField(model_name="videovisualizeranalysis", name="raw_track_count", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="videovisualizeranalysis", name="discarded_short_tracks", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="videovisualizeranalysis", name="duplicate_tracks_merged", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="videovisualizeranalysis", name="ground_truth_pothole_count", field=models.PositiveIntegerField(blank=True, null=True)),
        migrations.AddField(model_name="videovisualizeranalysis", name="ground_truth_notes", field=models.TextField(blank=True)),
        migrations.AddField(model_name="videopotholetrack", name="best_segmentation_points", field=models.JSONField(blank=True, default=list)),
        migrations.RunPython(seed_quality_metadata, restore_auto_approved_status),
    ]
