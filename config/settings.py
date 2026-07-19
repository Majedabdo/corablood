import os
try:
    import dj_database_url
except ImportError:
    dj_database_url = None

from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-fallback-dev-key')

DEBUG = os.environ.get('DEBUG') == 'True'

ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', '*').split(',')
CSRF_TRUSTED_ORIGINS = [
    'https://corablood-ultimat.onrender.com',
    'http://127.0.0.1:8000',
    'http://localhost:8000'
]

# Security Settings for CSRF
CSRF_USE_SESSIONS = False         # Keep CSRF in cookie, NOT session (session-based CSRF causes logout on refresh)
CSRF_COOKIE_HTTPONLY = False
CSRF_COOKIE_SECURE = False
SESSION_COOKIE_SECURE = False
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Session Persistence — Donors stay logged in across refreshes and browser restarts
SESSION_EXPIRE_AT_BROWSER_CLOSE = False   # Don't logout when browser closes
SESSION_COOKIE_AGE = 60 * 60 * 24 * 14   # 14 days (in seconds)
SESSION_SAVE_EVERY_REQUEST = True          # Refresh session expiry on every request

AUTH_USER_MODEL = 'core.User'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'
LOGIN_URL = '/login/'

try:
    import jazzmin
    HAS_JAZZMIN = True
except ImportError:
    HAS_JAZZMIN = False

INSTALLED_APPS = [
    *(['jazzmin'] if HAS_JAZZMIN else []),
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'rest_framework',
    'corsheaders',
    
    # Custom Apps
    'core',
    'donors',
    'clinical',
    'inventory',
    'orders',
    'ai_manager',
]

JAZZMIN_SETTINGS = {
    'site_title': 'CoraBlood Admin',
    'site_header': 'CoraBlood Blood Bank',
    'site_brand': 'CoraBlood',
    'welcome_sign': 'Welcome to CoraBlood Management System',
    'copyright': 'CoraBlood Medical Systems',
    'show_sidebar': True,
    'navigation_expanded': True,
    'icons': {
        'auth': 'fas fa-users-cog',
        'auth.user': 'fas fa-user',
        'auth.Group': 'fas fa-users',
        'clinical.DonorWorkflow': 'fas fa-heartbeat',
        'clinical.Question': 'fas fa-question-circle',
        'clinical.VitalSigns': 'fas fa-stethoscope',
        'clinical.BloodDraw': 'fas fa-tint',
        'clinical.LabResult': 'fas fa-flask',
        'donors.Donor': 'fas fa-user-injured',
        'inventory.BloodComponent': 'fas fa-boxes',
        'orders.Order': 'fas fa-clipboard-list',
    },
    'default_icon_parents': 'fas fa-chevron-circle-right',
    'default_icon_children': 'fas fa-circle',
    'use_google_fonts_cdn': True,
    'show_ui_builder': False,
    'changeform_format': 'horizontal_tabs',
    'language_chooser': False,
    'custom_css': 'admin/css/custom_admin.css',
    'custom_js': 'admin/js/custom_admin.js',
}

JAZZMIN_UI_TWEAKS = {
    'theme': 'default',
    'dark_mode_theme': None,
    'navbar': 'navbar-white navbar-light',
    'sidebar': 'sidebar-light-primary',
    'brand_colour': 'navbar-light',
    'accent': 'accent-primary',
    'navbar_fixed': True,
    'sidebar_fixed': True,
    'sidebar_nav_child_indent': True,
}

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'], # For global templates
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# Database Configuration
# We prioritize DATABASE_URL (Render environment)
if os.environ.get('DATABASE_URL') and dj_database_url:
    DATABASES = {
        'default': dj_database_url.config(conn_max_age=600, ssl_require=True)
    }
elif os.environ.get('DB_HOST'):
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.environ.get('DB_NAME', 'corablood'),
            'USER': os.environ.get('DB_USER', 'postgres'),
            'PASSWORD': os.environ.get('DB_PASSWORD', 'postgres'),
            'HOST': os.environ.get('DB_HOST', 'db'),
            'PORT': os.environ.get('DB_PORT', '5432'),
        }
    }
else:
    # Default to SQLite for local development
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

# WhiteNoise storage for efficient caching of static files
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

CORS_ALLOW_ALL_ORIGINS = True # For Dev

# Django REST Framework Settings
try:
    import django_filters
    HAS_DJANGO_FILTERS = True
except ImportError:
    HAS_DJANGO_FILTERS = False

REST_FRAMEWORK = {
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 25,
    'DEFAULT_FILTER_BACKENDS': [
        *(['django_filters.rest_framework.DjangoFilterBackend'] if HAS_DJANGO_FILTERS else []),
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
}

