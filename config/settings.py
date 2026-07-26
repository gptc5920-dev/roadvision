import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv


# =============================================================================
# BASE CONFIGURATION
# =============================================================================

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def env_list(name, default=""):
    return [
        item.strip()
        for item in os.environ.get(name, default).split(",")
        if item.strip()
    ]


ENVIRONMENT = os.environ.get(
    "ROADVISION_ENV",
    "development",
).strip().lower()

IS_PRODUCTION = ENVIRONMENT == "production"

DEBUG = env_bool(
    "DJANGO_DEBUG",
    not IS_PRODUCTION,
)

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-roadvision-local-development",
)

if IS_PRODUCTION and (
    SECRET_KEY.startswith("django-insecure-")
    or "replace-with" in SECRET_KEY.lower()
    or len(SECRET_KEY) < 50
):
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY must be set to a strong, non-placeholder secret in production."
    )


# =============================================================================
# HOST AND CSRF CONFIGURATION
# =============================================================================

ALLOWED_HOSTS = env_list(
    "DJANGO_ALLOWED_HOSTS",
    "localhost,127.0.0.1,testserver",
)

if IS_PRODUCTION and (not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS):
    raise ImproperlyConfigured(
        "DJANGO_ALLOWED_HOSTS must contain explicit production hostnames."
    )

CSRF_TRUSTED_ORIGINS = env_list(
    "DJANGO_CSRF_TRUSTED_ORIGINS",
)


# =============================================================================
# APPLICATIONS
# =============================================================================

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # Project applications
    "console",
]


# =============================================================================
# MIDDLEWARE
# =============================================================================

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # WhiteNoise must be immediately after SecurityMiddleware.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


# =============================================================================
# URL AND WSGI
# =============================================================================

ROOT_URLCONF = "config.urls"

WSGI_APPLICATION = "config.wsgi.application"


# =============================================================================
# TEMPLATES
# =============================================================================

template_options = {
    "context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ],
}
if ENVIRONMENT == "production":
    template_options["loaders"] = [
        (
            "django.template.loaders.cached.Loader",
            [
                "django.template.loaders.filesystem.Loader",
                "django.template.loaders.app_directories.Loader",
            ],
        )
    ]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": ENVIRONMENT != "production",
        "OPTIONS": template_options,
    },
]


# =============================================================================
# DATABASE
# Separate MySQL resource in Coolify
# =============================================================================

if IS_PRODUCTION:
    required_mysql_variables = [
        "MYSQL_DATABASE",
        "MYSQL_USER",
        "MYSQL_PASSWORD",
        "MYSQL_HOST",
    ]

    missing_mysql_variables = [
        variable
        for variable in required_mysql_variables
        if not os.environ.get(variable)
    ]

    if missing_mysql_variables:
        raise ImproperlyConfigured(
            "Missing MySQL environment variables: "
            + ", ".join(missing_mysql_variables)
        )

MYSQL_HOST = os.environ.get(
    "MYSQL_HOST",
    "127.0.0.1",
)

if IS_PRODUCTION and MYSQL_HOST in {"127.0.0.1", "localhost"}:
    raise ImproperlyConfigured(
        "MYSQL_HOST cannot be localhost or 127.0.0.1 when MySQL "
        "is a separate Coolify resource. Use its internal hostname."
    )

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": os.environ.get(
            "MYSQL_DATABASE",
            "roadsense",
        ),
        "USER": os.environ.get(
            "MYSQL_USER",
            "root",
        ),
        "PASSWORD": os.environ.get(
            "MYSQL_PASSWORD",
            "",
        ),
        "HOST": MYSQL_HOST,
        "PORT": os.environ.get(
            "MYSQL_PORT",
            "3306",
        ),
        "CONN_MAX_AGE": int(
            os.environ.get(
                "MYSQL_CONN_MAX_AGE",
                "60",
            )
        ),
        "CONN_HEALTH_CHECKS": True,
        "TEST": {
            "NAME": os.environ.get(
                "MYSQL_TEST_DATABASE",
                "roadsense_test",
            ),
        },
        "OPTIONS": {
            "charset": "utf8mb4",
            "init_command": (
                "SET sql_mode='STRICT_TRANS_TABLES'"
            ),
            "connect_timeout": int(
                os.environ.get(
                    "MYSQL_CONNECT_TIMEOUT",
                    "10",
                )
            ),
        },
    }
}
if ENVIRONMENT == "production":
    database_settings = DATABASES["default"]
    if database_settings["USER"] == "root":
        raise ImproperlyConfigured("Production must use a restricted database account, not root.")
    if not database_settings["PASSWORD"] or "replace-with" in database_settings["PASSWORD"].lower():
        raise ImproperlyConfigured("MYSQL_PASSWORD must be set to a non-placeholder production secret.")


# =============================================================================
# PASSWORD VALIDATION
# =============================================================================

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "UserAttributeSimilarityValidator"
        ),
    },
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "MinimumLengthValidator"
        ),
    },
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "CommonPasswordValidator"
        ),
    },
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "NumericPasswordValidator"
        ),
    },
]


# =============================================================================
# INTERNATIONALIZATION
# =============================================================================

LANGUAGE_CODE = "en-us"

TIME_ZONE = "Asia/Manila"

USE_I18N = True

USE_TZ = True


# =============================================================================
# STATIC FILES
# =============================================================================

STATIC_URL = "/static/"

STATIC_DIRECTORY = BASE_DIR / "static"
STATICFILES_DIRS = [STATIC_DIRECTORY] if STATIC_DIRECTORY.exists() else []
STATIC_ROOT = Path(os.environ.get("STATIC_ROOT", BASE_DIR / "staticfiles"))

MEDIA_URL = "/media/"
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", BASE_DIR / "media"))

AWS_STORAGE_BUCKET_NAME = os.environ.get("AWS_STORAGE_BUCKET_NAME", "")
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
if AWS_STORAGE_BUCKET_NAME:
    INSTALLED_APPS.append("storages")
    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.s3.S3Storage",
            "OPTIONS": {
                "bucket_name": AWS_STORAGE_BUCKET_NAME,
                "region_name": os.environ.get("AWS_S3_REGION_NAME"),
                "endpoint_url": os.environ.get("AWS_S3_ENDPOINT_URL") or None,
                "access_key": os.environ.get("AWS_ACCESS_KEY_ID"),
                "secret_key": os.environ.get("AWS_SECRET_ACCESS_KEY"),
                "default_acl": None,
                "querystring_auth": env_bool("AWS_QUERYSTRING_AUTH", True),
                "querystring_expire": int(os.environ.get("AWS_QUERYSTRING_EXPIRE", "900")),
                "file_overwrite": False,
            },
        },
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
    }
WHITENOISE_MAX_AGE = 31536000 if ENVIRONMENT == "production" else 0


# =============================================================================
# FILE UPLOAD SETTINGS
# =============================================================================

FILE_UPLOAD_MAX_MEMORY_SIZE = int(
    os.environ.get(
        "FILE_UPLOAD_MAX_MEMORY_SIZE",
        "2621440",
    )
)

DATA_UPLOAD_MAX_MEMORY_SIZE = int(
    os.environ.get(
        "DATA_UPLOAD_MAX_MEMORY_SIZE",
        "10485760",
    )
)


# =============================================================================
# ROADVISION SETTINGS
# =============================================================================

LOGIN_MAX_ATTEMPTS = int(
    os.environ.get(
        "LOGIN_MAX_ATTEMPTS",
        "5",
    )
)

LOGIN_LOCKOUT_SECONDS = int(
    os.environ.get(
        "LOGIN_LOCKOUT_SECONDS",
        "300",
    )
)

ALLOW_PRIVATE_STREAMS = env_bool(
    "ALLOW_PRIVATE_STREAMS",
    False,
)

ANALYSIS_LEASE_SECONDS = int(
    os.environ.get(
        "ANALYSIS_LEASE_SECONDS",
        "300",
    )
)

ANALYSIS_MAX_ATTEMPTS = int(
    os.environ.get(
        "ANALYSIS_MAX_ATTEMPTS",
        "3",
    )
)

LIVE_PREVIEW_INTERVAL_SECONDS = max(
    0.25,
    float(
        os.environ.get(
            "LIVE_PREVIEW_INTERVAL_SECONDS",
            "1",
        )
    ),
)

CONTINUOUS_STREAM_RECONNECT_SECONDS = max(
    0.2,
    float(
        os.environ.get(
            "CONTINUOUS_STREAM_RECONNECT_SECONDS",
            "2",
        )
    ),
)


# =============================================================================
# DATASET SETTINGS
# =============================================================================

DATASET_MIN_TRAIN_IMAGES = int(
    os.environ.get(
        "DATASET_MIN_TRAIN_IMAGES",
        "50",
    )
)

DATASET_MIN_VAL_IMAGES = int(
    os.environ.get(
        "DATASET_MIN_VAL_IMAGES",
        "10",
    )
)

DATASET_MIN_TEST_IMAGES = int(
    os.environ.get(
        "DATASET_MIN_TEST_IMAGES",
        "10",
    )
)

MODEL_MIN_MAP50 = float(
    os.environ.get(
        "MODEL_MIN_MAP50",
        "0.50",
    )
)

MODEL_REQUIRE_LOCAL_EVALUATION = env_bool(
    "MODEL_REQUIRE_LOCAL_EVALUATION",
    True,
)

ALLOW_DETECTION_MODE = env_bool(
    "ALLOW_DETECTION_MODE",
    env_bool(
        "ALLOW_LEGACY_DETECTION_MODE",
        False,
    ),
)

DETECTION_MASK_REFINEMENT = env_bool(
    "DETECTION_MASK_REFINEMENT",
    False,
)

DETECTION_MASK_MAX_SIZE = max(
    64,
    min(
        512,
        int(
            os.environ.get(
                "DETECTION_MASK_MAX_SIZE",
                "192",
            )
        ),
    ),
)

MODEL_MIN_LOCAL_TEST_IMAGES = int(
    os.environ.get(
        "MODEL_MIN_LOCAL_TEST_IMAGES",
        str(DATASET_MIN_TEST_IMAGES),
    )
)

AUTO_LABEL_CONFIDENCE = float(
    os.environ.get(
        "AUTO_LABEL_CONFIDENCE",
        "0.25",
    )
)

AUTO_LABEL_IOU = float(
    os.environ.get(
        "AUTO_LABEL_IOU",
        "0.45",
    )
)


# =============================================================================
# TRAINING SETTINGS
# =============================================================================

AUTO_TRAIN_MODEL = os.environ.get(
    "AUTO_TRAIN_MODEL",
    "yolo11s-seg",
)

AUTO_TRAIN_EPOCHS = int(
    os.environ.get(
        "AUTO_TRAIN_EPOCHS",
        "100",
    )
)

AUTO_TRAIN_BATCH_SIZE = int(
    os.environ.get(
        "AUTO_TRAIN_BATCH_SIZE",
        "16",
    )
)

AUTO_TRAIN_IMAGE_SIZE = int(
    os.environ.get(
        "AUTO_TRAIN_IMAGE_SIZE",
        "512",
    )
)

AUTO_TRAIN_DEVICE = os.environ.get(
    "AUTO_TRAIN_DEVICE",
    "cpu",
)

AUTO_START_ANALYSIS_WORKER = env_bool(
    "AUTO_START_ANALYSIS_WORKER",
    not IS_PRODUCTION,
)


# =============================================================================
# COOLIFY / TRAEFIK REVERSE PROXY
# =============================================================================

SECURE_PROXY_SSL_HEADER = (
    "HTTP_X_FORWARDED_PROTO",
    "https",
)

USE_X_FORWARDED_HOST = True

# =============================================================================
# COOKIE AND SECURITY SETTINGS
# =============================================================================

CACHE_TABLE = os.environ.get("DJANGO_CACHE_TABLE", "roadvision_cache")
CACHES = {
    "default": {
        "BACKEND": (
            "django.core.cache.backends.db.DatabaseCache"
            if ENVIRONMENT == "production"
            else "django.core.cache.backends.locmem.LocMemCache"
        ),
        "LOCATION": CACHE_TABLE if ENVIRONMENT == "production" else "roadvision-local",
        "TIMEOUT": 300,
        "OPTIONS": {"MAX_ENTRIES": 10000},
    }
}

SESSION_COOKIE_HTTPONLY = True

CSRF_COOKIE_HTTPONLY = True

SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = env_bool("DJANGO_SECURE_COOKIES", ENVIRONMENT == "production")
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", ENVIRONMENT == "production")
SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", "31536000" if ENVIRONMENT == "production" else "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_HSTS_INCLUDE_SUBDOMAINS", ENVIRONMENT == "production")
SECURE_HSTS_PRELOAD = env_bool("DJANGO_HSTS_PRELOAD", False)
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"


# =============================================================================
# LOGGING
# =============================================================================

LOG_LEVEL = os.environ.get(
    "LOG_LEVEL",
    "INFO",
).upper()

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "roadvision": {
            "format": (
                "{asctime} {levelname} "
                "{name} {message}"
            ),
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "roadvision",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": os.environ.get("ROOT_LOG_LEVEL", "WARNING"),
    },
    "loggers": {
        "console": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO"), "propagate": False},
        "django.request": {
            "handlers": ["console"],
            "level": os.environ.get("DJANGO_REQUEST_LOG_LEVEL", "WARNING"),
            "propagate": False,
        },
        "django.security": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}


# =============================================================================
# AUTHENTICATION REDIRECTS
# =============================================================================

LOGIN_URL = "auth"

LOGIN_REDIRECT_URL = "admin_video_analyzer"

LOGOUT_REDIRECT_URL = "home"


# =============================================================================
# DEFAULT PRIMARY KEY
# =============================================================================

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
