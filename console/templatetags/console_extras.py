from django import template


register = template.Library()


@register.filter
def role_of(user):
    return getattr(getattr(user, "console_role", None), "role", "viewer")


@register.filter
def profile_name(user):
    profile = getattr(user, "profile", None)
    return getattr(profile, "full_name", "") or user.email or user.username
