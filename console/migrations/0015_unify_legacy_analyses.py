import hashlib

from django.db import migrations


def copy_legacy_analyses(apps, schema_editor):
    VideoAnalysis = apps.get_model("console", "VideoAnalysis")
    UnifiedAnalysis = apps.get_model("console", "VideoVisualizerAnalysis")
    Track = apps.get_model("console", "VideoPotholeTrack")

    severity_map = {"medium": "moderate"}
    for legacy in VideoAnalysis.objects.prefetch_related("events").all():
        if UnifiedAnalysis.objects.filter(legacy_analysis_id=legacy.pk).exists():
            continue

        events = list(legacy.events.all().order_by("timecode_seconds", "id"))
        confidences = [event.confidence / 100 for event in events]
        source_name = legacy.original_filename or str(legacy.dataset_sample_id or f"legacy-{legacy.pk}")
        analysis = UnifiedAnalysis.objects.create(
            legacy_analysis_id=legacy.pk,
            video=legacy.uploaded_video.name if legacy.uploaded_video else "",
            original_filename=source_name,
            file_hash=hashlib.sha256(f"legacy:{legacy.pk}:{source_name}".encode()).hexdigest(),
            file_type=(source_name.rsplit(".", 1)[-1].lower() if "." in source_name else "legacy")[:12],
            duration_seconds=legacy.duration_seconds,
            source_type="upload" if legacy.uploaded_video else "upload",
            road_section=legacy.road_name,
            chainage_station=legacy.chainage_station,
            route_metadata={
                "legacy_analysis_id": legacy.pk,
                "route_start": legacy.route_start,
                "route_end": legacy.route_end,
                "barangay": legacy.barangay,
                "city": legacy.city,
            },
            status="complete" if legacy.status == "complete" else "failed",
            frames_processed=legacy.frames_processed,
            total_unique_potholes=len(events),
            total_detections=len(events),
            average_confidence=(sum(confidences) / len(confidences)) if confidences else None,
            highest_confidence=max(confidences) if confidences else None,
            lowest_confidence=min(confidences) if confidences else None,
            processing_time_ms=0,
            average_processing_fps=legacy.inference_fps,
            created_by_id=legacy.created_by_id,
            started_at=legacy.analyzed_at,
            finished_at=legacy.analyzed_at,
            error_message="" if legacy.status == "complete" else "Imported from an incomplete legacy analysis.",
        )

        timeline = []
        for index, event in enumerate(events, start=1):
            confidence = event.confidence / 100
            relative_size = (event.bbox_w / 100) * (event.bbox_h / 100)
            snapshot_name = ""
            if event.snapshot_image and event.snapshot_image.storage.exists(event.snapshot_image.name):
                snapshot_name = event.snapshot_image.name
            Track.objects.create(
                analysis_id=analysis.pk,
                track_id=index,
                first_frame=0,
                last_frame=0,
                first_timestamp=event.timecode_seconds,
                last_timestamp=event.timecode_seconds,
                appearance_count=1,
                average_confidence=confidence,
                highest_confidence=confidence,
                lowest_confidence=confidence,
                best_bbox={
                    "center_x": (event.bbox_x + event.bbox_w / 2) / 100,
                    "center_y": (event.bbox_y + event.bbox_h / 2) / 100,
                    "width": event.bbox_w / 100,
                    "height": event.bbox_h / 100,
                },
                severity=severity_map.get(event.severity, event.severity),
                relative_bbox_size=relative_size,
                measurement_basis="visual-estimate",
                road_section=event.road_name,
                review_status="unresolved",
                remarks="Imported from legacy detection; physical dimensions require calibration.",
                snapshot_frame=snapshot_name,
            )
            timeline.append(
                {
                    "track_id": index,
                    "timestamp": float(event.timecode_seconds),
                    "frame": 0,
                    "percent": round((float(event.timecode_seconds) / float(legacy.duration_seconds or 1)) * 100, 3),
                    "status": "unresolved",
                    "severity": severity_map.get(event.severity, event.severity),
                    "confidence": confidence,
                }
            )

        analysis.timeline_markers = timeline
        analysis.save(update_fields=["timeline_markers"])
        UnifiedAnalysis.objects.filter(pk=analysis.pk).update(created_at=legacy.created_at)


def remove_legacy_copies(apps, schema_editor):
    apps.get_model("console", "VideoVisualizerAnalysis").objects.filter(legacy_analysis__isnull=False).delete()


class Migration(migrations.Migration):
    dependencies = [("console", "0014_production_readiness")]

    operations = [migrations.RunPython(copy_legacy_analyses, remove_legacy_copies)]
