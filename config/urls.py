from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path


def health_check(request):
    return JsonResponse({
        "status": "healthy",
        "application": "RoadVision",
    })


urlpatterns = [
    path("health/", health_check, name="health_check"),
    path("django-admin/", admin.site.urls),
    path("", include("console.urls")),
]

# Development only
if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )
