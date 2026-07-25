from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("console", "0009_mask_engineering_measurements"),
    ]

    operations = [
        migrations.AddField(
            model_name="videoanalysis",
            name="barangay",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="videoanalysis",
            name="chainage_station",
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name="videoanalysis",
            name="city",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="videoanalysis",
            name="road_name",
            field=models.CharField(blank=True, max_length=160),
        ),
        migrations.AddField(
            model_name="videoanalysis",
            name="route_end",
            field=models.CharField(blank=True, max_length=180),
        ),
        migrations.AddField(
            model_name="videoanalysis",
            name="route_start",
            field=models.CharField(blank=True, max_length=180),
        ),
    ]
