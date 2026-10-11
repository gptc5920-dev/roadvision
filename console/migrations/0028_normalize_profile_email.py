from django.conf import settings
from django.db import migrations


def normalize_email(apps, schema_editor):
    Profile = apps.get_model("console", "Profile")
    alias = schema_editor.connection.alias
    for profile in Profile.objects.using(alias).select_related("user").iterator():
        user = profile.user
        if not user.email and profile.email:
            user.email = profile.email
            user.save(using=alias, update_fields=["email"])
        elif profile.email and user.email.casefold() != profile.email.casefold():
            raise RuntimeError(
                f"Profile {profile.pk} has conflicting emails. Reconcile its user and profile email before migrating."
            )


def restore_email(apps, schema_editor):
    Profile = apps.get_model("console", "Profile")
    alias = schema_editor.connection.alias
    for profile in Profile.objects.using(alias).select_related("user").iterator():
        profile.email = profile.user.email
        profile.save(using=alias, update_fields=["email"])


class Migration(migrations.Migration):
    dependencies = [
        ("console", "0027_datasetimage_rv_dataset_active_time_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.RunPython(normalize_email, restore_email),
        migrations.RemoveField(model_name="profile", name="email"),
    ]
