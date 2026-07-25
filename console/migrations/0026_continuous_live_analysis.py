from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("console", "0025_model_evaluation_and_runtime_quality")]

    operations = [
        migrations.AddField(
            model_name="videovisualizeranalysis",
            name="is_continuous",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="videovisualizeranalysis",
            name="stop_requested",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="videovisualizeranalysis",
            name="live_preview_frame",
            field=models.ImageField(blank=True, upload_to="video_visualizer/live/%Y/%m/%d/"),
        ),
    ]
