from django.db import migrations


def remove_missing_snapshots(apps, schema_editor):
    Track = apps.get_model("console", "VideoPotholeTrack")
    for track in Track.objects.exclude(snapshot_frame="").iterator():
        if not track.snapshot_frame.storage.exists(track.snapshot_frame.name):
            track.snapshot_frame = ""
            track.save(update_fields=["snapshot_frame"])


class Migration(migrations.Migration):
    dependencies = [("console", "0016_detection_artifact_storage")]

    operations = [migrations.RunPython(remove_missing_snapshots, migrations.RunPython.noop)]
