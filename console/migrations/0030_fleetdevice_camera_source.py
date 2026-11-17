from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("console", "0029_training_worker_leases")]

    operations = [
        migrations.AddField(
            model_name="fleetdevice",
            name="camera_source",
            field=models.CharField(
                choices=[
                    ("stream", "Stream URL"), ("dashcam", "Dashcam"),
                    ("laptop", "Laptop camera"), ("mobile", "Mobile camera"),
                ],
                default="stream",
                max_length=20,
            ),
        ),
    ]
