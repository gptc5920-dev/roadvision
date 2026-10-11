import json

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.models import Q

from console.authz import user_role


class Command(BaseCommand):
    help = "Show the connected database and a saved account without exposing passwords."

    def add_arguments(self, parser):
        parser.add_argument("identifier", help="The account's username or email address.")

    def handle(self, *args, **options):
        identifier = options["identifier"].strip()
        if not identifier:
            raise CommandError("Enter a username or email address.")
        User = get_user_model()
        database = connection.settings_dict
        connected_name = str(database["NAME"])
        if connection.vendor == "mysql":
            with connection.cursor() as cursor:
                cursor.execute("SELECT DATABASE()")
                connected_name = cursor.fetchone()[0]
        accounts = User.objects.filter(
            Q(username__iexact=identifier) | Q(email__iexact=identifier)
        ).select_related("console_role")[:10]
        report = {
            "settings": settings.SETTINGS_MODULE,
            "database": {
                "engine": database["ENGINE"],
                "host": database.get("HOST", ""),
                "port": database.get("PORT", ""),
                "name": connected_name,
                "userTable": User._meta.db_table,
            },
            "accounts": [
                {
                    "id": account.pk,
                    "username": account.username,
                    "email": account.email,
                    "isActive": account.is_active,
                    "isSuperuser": account.is_superuser,
                    "hasUsablePassword": account.has_usable_password(),
                    "storedRole": getattr(getattr(account, "console_role", None), "role", None),
                    "effectiveRole": user_role(account),
                }
                for account in accounts
            ],
        }
        self.stdout.write(json.dumps(report, indent=2))
        if not report["accounts"]:
            raise CommandError("Account not found in the database shown above.")
