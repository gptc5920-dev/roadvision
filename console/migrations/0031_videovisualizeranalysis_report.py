import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("console", "0030_fleetdevice_camera_source")]

    operations = [
        migrations.AddField(
            model_name="videovisualizeranalysis",
            name="report",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="video_analyses", to="console.potholereport",
            ),
        ),
    ]
