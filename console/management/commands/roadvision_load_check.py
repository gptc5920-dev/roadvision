import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand, CommandError
from django.test import Client


class Command(BaseCommand):
    help = "Run a local authenticated read-only concurrency smoke check."

    def add_arguments(self, parser):
        parser.add_argument("--requests", type=int, default=25)
        parser.add_argument("--concurrency", type=int, default=5)
        parser.add_argument("--path", default="/_authenticated/admin/video-analyzer/")

    def handle(self, *args, **options):
        user = get_user_model().objects.filter(is_active=True, console_role__role__in=["admin", "engineer"]).first()
        if user is None:
            raise CommandError("No active engineer account is available for the load check.")

        def request_once(_):
            client = Client()
            client.force_login(user)
            started = time.perf_counter()
            response = client.get(options["path"])
            return response.status_code, (time.perf_counter() - started) * 1000, client.session.session_key

        results = []
        with ThreadPoolExecutor(max_workers=max(1, options["concurrency"])) as pool:
            futures = [pool.submit(request_once, index) for index in range(max(1, options["requests"]))]
            for future in as_completed(futures):
                results.append(future.result())
        failures = [status for status, _, _ in results if status != 200]
        latencies = sorted(duration for _, duration, _ in results)
        Session.objects.filter(session_key__in=[key for _, _, key in results if key]).delete()
        p95_index = max(0, int(len(latencies) * 0.95) - 1)
        self.stdout.write(
            f"requests={len(results)} failures={len(failures)} "
            f"median_ms={statistics.median(latencies):.1f} p95_ms={latencies[p95_index]:.1f} max_ms={max(latencies):.1f}"
        )
        if failures:
            raise CommandError(f"Load check returned {len(failures)} non-200 responses.")
