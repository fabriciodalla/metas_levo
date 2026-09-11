from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR.parent / ".env")

# Sem default: preferimos falhar alto (o processo nem sobe) a rodar com uma chave insegura
# conhecida publicamente — .env.example já traz um valor placeholder pra todo ambiente novo.
SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

# Origin do frontend (Vite dev server) — necessário porque o proxy do Vite preserva o header
# Origin do navegador mesmo reescrevendo o Host para o alvo interno (ver docker-compose.yml).
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=["http://localhost:5173"])

# Liga cookies só-HTTPS e HSTS por padrão sempre que DEBUG=False (deploy real, hoje acessado só
# via túnel Cloudflare, sempre HTTPS) — em DEBUG=True (dev local, http://localhost) fica desligado
# por padrão, senão o cookie de sessão nunca seria gravado/enviado e ninguém logaria localmente.
# Pode ser forçado nos dois sentidos via DJANGO_SECURE_COOKIES.
DJANGO_SECURE_COOKIES = env.bool("DJANGO_SECURE_COOKIES", default=not DEBUG)
SESSION_COOKIE_SECURE = DJANGO_SECURE_COOKIES
CSRF_COOKIE_SECURE = DJANGO_SECURE_COOKIES
SESSION_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
# O túnel Cloudflare encerra HTTPS na borda e repassa X-Forwarded-Proto pro backend — sem isso,
# o Django nunca reconhece a requisição como segura e o cabeçalho HSTS abaixo nunca seria emitido.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
if DJANGO_SECURE_COOKIES:
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "apps.accounts",
    "apps.hierarchy",
    "apps.catalog",
    "apps.cycles",
    "apps.allocations",
    "apps.audit",
    "apps.sales_history",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Precisa vir logo depois do SecurityMiddleware (ordem exigida pela doc do whitenoise). Em dev
    # (`runserver`, sem STATIC_ROOT/collectstatic) simplesmente não encontra nada pra servir e
    # repassa a requisição adiante — zero efeito fora de produção.
    "whitenoise.middleware.WhiteNoiseMiddleware",
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
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
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


DATABASES = {"default": env.db("DATABASE_URL", default=f'sqlite:///{BASE_DIR / "db.sqlite3"}')}

# Postgres externo do histórico de vendas (somente leitura, ver docs/architecture.md). Nenhum
# model/migração é atribuído a este alias — só cursor bruto no adaptador SalesHistoryProvider.
# Registrado só quando configurado; se vazio, nada tenta conectar.
if env("SALES_HISTORY_DATABASE_URL", default=""):
    DATABASES["sales_history"] = env.db("SALES_HISTORY_DATABASE_URL")

AUTH_USER_MODEL = "accounts.User"

# E-mail (link de definição/redefinição de senha). Sem SMTP configurado ainda: o backend padrão só
# imprime o e-mail no log do processo (`docker compose logs backend`). Configure
# DJANGO_EMAIL_BACKEND + as variáveis EMAIL_* quando houver um servidor de e-mail real.
EMAIL_BACKEND = env("DJANGO_EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="naoresponda@levo.local")

# URL do frontend, usada para montar o link enviado por e-mail (ex.: /redefinir-senha/...).
FRONTEND_URL = env("FRONTEND_URL", default="http://localhost:5173")

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Link de "definir/redefinir senha" enviado por e-mail: 24h em vez do padrão do Django (3 dias) —
# é um link de acesso inicial, uma janela mais curta é suficiente e mais segura.
PASSWORD_RESET_TIMEOUT = 60 * 60 * 24

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
# Só é usado em produção — `collectstatic` roda no entrypoint do container (Dockerfile.prod), nunca
# em dev (`runserver` serve os estáticos sozinho, sem precisar de STATIC_ROOT).
STATIC_ROOT = BASE_DIR / "staticfiles"

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    # Só nos endpoints de login e recuperação de senha (via throttle_scope nas views) — sem
    # isso, os dois aceitavam tentativas ilimitadas, viabilizando força bruta e uso do endpoint
    # de reset como disparador de e-mail em massa (achado A-03).
    "DEFAULT_THROTTLE_RATES": {
        "login": "15/min",
        "password_reset": "5/min",
    },
}
