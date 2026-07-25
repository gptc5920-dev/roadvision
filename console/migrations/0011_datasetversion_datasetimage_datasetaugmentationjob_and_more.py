
import console.models
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('console', '0010_videoanalysis_route_metadata'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='DatasetVersion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('version_number', models.PositiveIntegerField(unique=True)),
                ('train_percent', models.PositiveSmallIntegerField(default=70)),
                ('val_percent', models.PositiveSmallIntegerField(default=20)),
                ('test_percent', models.PositiveSmallIntegerField(default=10)),
                ('total_images', models.PositiveIntegerField(default=0)),
                ('total_annotations', models.PositiveIntegerField(default=0)),
                ('notes', models.CharField(blank=True, max_length=220)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('archived_at', models.DateTimeField(blank=True, null=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-version_number'],
            },
        ),
        migrations.CreateModel(
            name='DatasetImage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('dataset_id', models.CharField(default=console.models.dataset_image_id, max_length=32, unique=True)),
                ('image', models.ImageField(upload_to='training_dataset/originals/%Y/%m/%d/')),
                ('original_filename', models.CharField(max_length=255)),
                ('file_hash', models.CharField(max_length=64, unique=True)),
                ('file_size', models.PositiveIntegerField(default=0)),
                ('file_type', models.CharField(max_length=12)),
                ('width', models.PositiveIntegerField(default=0)),
                ('height', models.PositiveIntegerField(default=0)),
                ('split', models.CharField(choices=[('train', 'train'), ('val', 'val'), ('test', 'test')], default='train', max_length=12)),
                ('status', models.CharField(choices=[('unannotated', 'unannotated'), ('partial', 'partially annotated'), ('full', 'fully annotated'), ('approved', 'approved'), ('rejected', 'rejected')], default='unannotated', max_length=24)),
                ('source', models.CharField(choices=[('upload', 'upload'), ('detection', 'detection'), ('augmented', 'augmented')], default='upload', max_length=20)),
                ('uploaded_at', models.DateTimeField(auto_now_add=True)),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('review_notes', models.TextField(blank=True)),
                ('is_archived', models.BooleanField(default=False)),
                ('parent_image', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='augmented_images', to='console.datasetimage')),
                ('reviewed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='dataset_reviews', to=settings.AUTH_USER_MODEL)),
                ('uploaded_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='dataset_uploads', to=settings.AUTH_USER_MODEL)),
                ('dataset_version', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='images', to='console.datasetversion')),
            ],
            options={
                'ordering': ['-uploaded_at'],
            },
        ),
        migrations.CreateModel(
            name='DatasetAugmentationJob',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('options', models.JSONField(blank=True, default=dict)),
                ('generated_count', models.PositiveIntegerField(default=0)),
                ('status', models.CharField(default='ready', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('source_version', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='console.datasetversion')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='DatasetAuditLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('action', models.CharField(max_length=60)),
                ('message', models.CharField(max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('dataset_image', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='console.datasetimage')),
                ('dataset_version', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='console.datasetversion')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='PotholeAnnotation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('class_id', models.PositiveSmallIntegerField(default=0)),
                ('label', models.CharField(default='pothole', max_length=32)),
                ('center_x', models.DecimalField(decimal_places=6, max_digits=9)),
                ('center_y', models.DecimalField(decimal_places=6, max_digits=9)),
                ('width', models.DecimalField(decimal_places=6, max_digits=9)),
                ('height', models.DecimalField(decimal_places=6, max_digits=9)),
                ('confidence', models.DecimalField(blank=True, decimal_places=4, max_digits=5, null=True)),
                ('source', models.CharField(choices=[('manual', 'manual'), ('predicted', 'predicted'), ('augmented', 'augmented')], default='manual', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('image', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='annotations', to='console.datasetimage')),
            ],
            options={
                'ordering': ['image_id', 'id'],
            },
        ),
        migrations.CreateModel(
            name='TrainingSession',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('model_name', models.CharField(default='yolo11n', max_length=20)),
                ('epochs', models.PositiveIntegerField(default=50)),
                ('batch_size', models.PositiveIntegerField(default=16)),
                ('image_size', models.PositiveIntegerField(default=640)),
                ('learning_rate', models.DecimalField(decimal_places=6, default=0.01, max_digits=8)),
                ('device', models.CharField(default='cpu', max_length=60)),
                ('patience', models.PositiveIntegerField(default=20)),
                ('workers', models.PositiveIntegerField(default=2)),
                ('status', models.CharField(choices=[('queued', 'queued'), ('running', 'running'), ('complete', 'complete'), ('failed', 'failed')], default='queued', max_length=20)),
                ('progress', models.PositiveSmallIntegerField(default=0)),
                ('current_epoch', models.PositiveIntegerField(default=0)),
                ('train_loss', models.DecimalField(blank=True, decimal_places=5, max_digits=10, null=True)),
                ('val_loss', models.DecimalField(blank=True, decimal_places=5, max_digits=10, null=True)),
                ('precision', models.DecimalField(blank=True, decimal_places=4, max_digits=7, null=True)),
                ('recall', models.DecimalField(blank=True, decimal_places=4, max_digits=7, null=True)),
                ('map50', models.DecimalField(blank=True, decimal_places=4, max_digits=7, null=True)),
                ('map5095', models.DecimalField(blank=True, decimal_places=4, max_digits=7, null=True)),
                ('metrics', models.JSONField(blank=True, default=dict)),
                ('model_file', models.CharField(blank=True, max_length=255)),
                ('results_dir', models.CharField(blank=True, max_length=255)),
                ('error_message', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('dataset_version', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='console.datasetversion')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='DetectionTest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('image', models.ImageField(upload_to='training_dataset/tests/%Y/%m/%d/')),
                ('result_image', models.ImageField(blank=True, upload_to='training_dataset/test_results/%Y/%m/%d/')),
                ('original_filename', models.CharField(max_length=255)),
                ('confidence_threshold', models.PositiveSmallIntegerField(default=50)),
                ('iou_threshold', models.PositiveSmallIntegerField(default=45)),
                ('detections', models.JSONField(blank=True, default=list)),
                ('detection_count', models.PositiveIntegerField(default=0)),
                ('processing_time_ms', models.PositiveIntegerField(default=0)),
                ('status', models.CharField(default='pending', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('model_session', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='console.trainingsession')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]
