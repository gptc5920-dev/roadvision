from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import AppRole, Profile, UserRole


@receiver(post_save, sender=get_user_model())
def ensure_console_identity(sender, instance, created, raw=False, **kwargs):
    if raw or not created:
        return

    full_name = f"{instance.first_name} {instance.last_name}".strip() or instance.username.split("@")[0]
    Profile.objects.get_or_create(
        user=instance,
        defaults={"full_name": full_name, "email": instance.email or instance.username},
    )

    role = AppRole.ADMIN if not UserRole.objects.filter(role=AppRole.ADMIN).exists() else AppRole.VIEWER
    UserRole.objects.get_or_create(user=instance, defaults={"role": role})
