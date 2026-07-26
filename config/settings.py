import os
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


ENVIRONMENT = os.environ.get("ROADVISION_ENV", "development").lower()
DEBUG = env_bool("DJANGO_DEBUG", ENVIRONMENT != "production")
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "django-insecure-roadvision-local-development")

# Defaulting to '*' in non-development if not explicitly provided avoids 404/400 Host header drops in Coolify
default_allowed_hosts = "*" if ENVIRONMENT == "production" else "localhost,127.0.0.1,testserver"
ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("DJANGO_ALLOWED_HOSTS", default_allowed_hosts).split(",")
    if host.strip()
]

if ENVIRONMENT == "production" and SECRET_KEY.startswith("django-insecure-"):
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set to a strong secret in production.")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "console",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": os.environ.get("MYSQL_DATABASE", "roadsense"),
        "USER": os.environ.get("MYSQL_USER", "root"),
        "PASSWORD": os.environ.get("MYSQL_PASSWORD", ""),
        "HOST": os.environ.get("MYSQL_HOST", "127.0.0.1"),
        "PORT": os.environ.get("MYSQL_PORT", "3306"),
        "CONN_MAX_AGE": int(os.environ.get("MYSQL_CONN_MAX_AGE", "60")),
        "CONN_HEALTH_CHECKS": True,
        "TEST": {"NAME": os.environ.get("MYSQL_TEST_DATABASE", "roadsense_test")},
        "OPTIONS": {
            "charset": "utf8mb4",
            "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
        },
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Singapore"
USE_I18N = True
USE_TZ = True

# Static files setup
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

AWS_STORAGE_BUCKET_NAME = os.environ.get("AWS_STORAGE_BUCKET_NAME", "")
if AWS_STORAGE_BUCKET_NAME:
    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.s3.S3Storage",
            "OPTIONS": {
                "bucket_name": AWS_STORAGE_BUCKET_NAME,
                "region_name": os.environ.get("AWS_S3_REGION_NAME"),
                "endpoint_url": os.environ.get("AWS_S3_ENDPOINT_URL") or None,
                "default_acl": None,
                "querystring_auth": True,
            },
        },
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }

# Keep large uploads on disk instead of duplicating them in web-worker memory.
FILE_UPLOAD_MAX_MEMORY_SIZE = int(os.environ.get("FILE_UPLOAD_MAX_MEMORY_SIZE", str(2_621_440)))
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.environ.get("DATA_UPLOAD_MAX_MEMORY_SIZE", str(10_485_760)))

LOGIN_MAX_ATTEMPTS = int(os.environ.get("LOGIN_MAX_ATTEMPTS", "5"))
LOGIN_LOCKOUT_SECONDS = int(os.environ.get("LOGIN_LOCKOUT_SECONDS", "300"))
ALLOW_PRIVATE_STREAMS = env_bool("ALLOW_PRIVATE_STREAMS", False)
ANALYSIS_LEASE_SECONDS = int(os.environ.get("ANALYSIS_LEASE_SECONDS", "300"))
ANALYSIS_MAX_ATTEMPTS = int(os.environ.get("ANALYSIS_MAX_ATTEMPTS", "3"))
LIVE_PREVIEW_INTERVAL_SECONDS = max(0.25, float(os.environ.get("LIVE_PREVIEW_INTERVAL_SECONDS", "1")))
CONTINUOUS_STREAM_RECONNECT_SECONDS = max(0.2, float(os.environ.get("CONTINUOUS_STREAM_RECONNECT_SECONDS", "2")))
DATASET_MIN_TRAIN_IMAGES = int(os.environ.get("DATASET_MIN_TRAIN_IMAGES", "50"))
DATASET_MIN_VAL_IMAGES = int(os.environ.get("DATASET_MIN_VAL_IMAGES", "10"))
DATASET_MIN_TEST_IMAGES = int(os.environ.get("DATASET_MIN_TEST_IMAGES", "10"))
MODEL_MIN_MAP50 = float(os.environ.get("MODEL_MIN_MAP50", "0.50"))
MODEL_REQUIRE_LOCAL_EVALUATION = env_bool("MODEL_REQUIRE_LOCAL_EVALUATION", True)
ALLOW_DETECTION_MODE = env_bool(
    "ALLOW_DETECTION_MODE",
    env_bool("ALLOW_LEGACY_DETECTION_MODE", False),
)
DETECTION_MASK_REFINEMENT = env_bool("DETECTION_MASK_REFINEMENT", False)
DETECTION_MASK_MAX_SIZE = max(64, min(512, int(os.environ.get("DETECTION_MASK_MAX_SIZE", "192"))))
MODEL_MIN_LOCAL_TEST_IMAGES = int(os.environ.get("MODEL_MIN_LOCAL_TEST_IMAGES", str(DATASET_MIN_TEST_IMAGES)))
AUTO_LABEL_CONFIDENCE = float(os.environ.get("AUTO_LABEL_CONFIDENCE", "0.25"))
AUTO_LABEL_IOU = float(os.environ.get("AUTO_LABEL_IOU", "0.45"))
AUTO_TRAIN_MODEL = os.environ.get("AUTO_TRAIN_MODEL", "yolo11s-seg")
AUTO_TRAIN_EPOCHS = int(os.environ.get("AUTO_TRAIN_EPOCHS", "100"))
AUTO_TRAIN_BATCH_SIZE = int(os.environ.get("AUTO_TRAIN_BATCH_SIZE", "16"))
AUTO_TRAIN_IMAGE_SIZE = int(os.environ.get("AUTO_TRAIN_IMAGE_SIZE", "512"))
AUTO_TRAIN_DEVICE = os.environ.get("AUTO_TRAIN_DEVICE", "cpu")
AUTO_START_ANALYSIS_WORKER = env_bool("AUTO_START_ANALYSIS_WORKER", ENVIRONMENT != "production")

# Reverse Proxy / SSL Settings for Coolify & Traefik
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = env_bool("DJANGO_SECURE_COOKIES", ENVIRONMENT == "production")
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", ENVIRONMENT == "production")
SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", "31536000" if ENVIRONMENT == "production" else "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = ENVIRONMENT == "production"
SECURE_HSTS_PRELOAD = ENVIRONMENT == "production"
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "roadvision": {
            "format": "{asctime} {levelname} {name} {message}",
            "style": "{",
        }
    },
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "roadvision"}},
    "loggers": {
        "console": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO"), "propagate": False},
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "auth"
LOGIN_REDIRECT_URL = "admin_video_analyzer"
LOGOUT_REDIRECT_URL = "home"
