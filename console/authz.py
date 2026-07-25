from .models import AppRole


def user_role(user):
    if not user.is_authenticated:
        return None
    return getattr(getattr(user, "console_role", None), "role", AppRole.VIEWER)


def is_admin(user):
    return user_role(user) == AppRole.ADMIN


def is_staff_role(user):
    return user_role(user) in {AppRole.ADMIN, AppRole.ENGINEER}
