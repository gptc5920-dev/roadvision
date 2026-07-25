from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("console", "0023_analyzer_training_model"),
    ]

    operations = [
        migrations.AddField(
            model_name="fleetdevice",
            name="chainage_station",
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name="fleetdevice",
            name="road_section",
            field=models.CharField(blank=True, max_length=180),
        ),
        migrations.AddField(
            model_name="fleetdevice",
            name="stream_url",
            field=models.CharField(blank=True, max_length=500),
        ),
    ]
