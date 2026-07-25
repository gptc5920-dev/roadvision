from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("console", "0022_dataset_labeled_preview"),
    ]

    operations = [
        migrations.AddField(
            model_name="analyzerconfiguration",
            name="training_model",
            field=models.CharField(
                choices=[
                    ("yolo11n-seg", "YOLO11n segmentation"),
                    ("yolo11s-seg", "YOLO11s segmentation"),
                    ("yolo11m-seg", "YOLO11m segmentation"),
                    ("yolo11l-seg", "YOLO11l segmentation"),
                    ("yolo11x-seg", "YOLO11x segmentation"),
                    ("yolo26n-seg", "YOLO26n segmentation"),
                    ("yolo26s-seg", "YOLO26s segmentation"),
                    ("yolo26m-seg", "YOLO26m segmentation"),
                    ("yolo26l-seg", "YOLO26l segmentation"),
                    ("yolo26x-seg", "YOLO26x segmentation"),
                ],
                default="yolo11s-seg",
                max_length=20,
            ),
        ),
    ]
