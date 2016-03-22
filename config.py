import os
from datetime import timedelta


class Config(object):
    DEBUG = False
    ASSETS_DEBUG = False
    cache = False
    SEND_FILE_MAX_AGE_DEFAULT = 365 * 24 * 60 * 60  # 1 year
    manifest = True

    NOTIFY_LOG_LEVEL = 'DEBUG'
    NOTIFY_APP_NAME = 'admin'
    NOTIFY_LOG_PATH = '/var/log/notify/application.log'

    MAX_FAILED_LOGIN_COUNT = 10

    SESSION_COOKIE_NAME = 'notify_admin_session'
    SESSION_COOKIE_PATH = '/admin'
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = True
    PERMANENT_SESSION_LIFETIME = 3600  # seconds
    SESSION_REFRESH_EACH_REQUEST = True
    REMEMBER_COOKIE_NAME = 'notify_admin_remember_me'
    REMEMBER_COOKIE_PATH = '/admin'
    REMEMBER_COOKIE_DURATION = timedelta(days=1)
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = True

    API_HOST_NAME = os.getenv('API_HOST_NAME')

    ADMIN_CLIENT_USER_NAME = os.getenv('ADMIN_CLIENT_USER_NAME')
    ADMIN_CLIENT_SECRET = os.getenv('ADMIN_CLIENT_SECRET')

    WTF_CSRF_ENABLED = True
    HTTP_PROTOCOL = 'http'
    SECRET_KEY = os.getenv('SECRET_KEY')
    DANGEROUS_SALT = os.getenv('DANGEROUS_SALT')
    TOKEN_MAX_AGE_SECONDS = 3600
    EMAIL_EXPIRY_SECONDS = TOKEN_MAX_AGE_SECONDS * 24 * 7  # one week

    DEFAULT_SERVICE_LIMIT = 50

    MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10mb
    UPLOAD_FOLDER = '/tmp'

    HEADER_COLOUR = '#FFBF47'  # $yellow

    AWS_REGION = 'eu-west-1'

    SHOW_STYLEGUIDE = True

    EMAIL_DOMAIN_REGEXES = [
        "gov.uk",
        "mod.uk",
        "mil.uk",
        "ddc-mod.org",
        "slc.co.uk"
        "gov.scot",
        "parliament.uk",
        "nhs.uk",
        "nhs.net",
        "police.uk"]


class Development(Config):
    DEBUG = True
    API_HOST_NAME = 'http://localhost:6011'
    ADMIN_CLIENT_USER_NAME = 'dev-notify-admin'
    ADMIN_CLIENT_SECRET = 'dev-notify-secret-key'
    WTF_CSRF_ENABLED = False
    REMEMBER_COOKIE_SECURE = False
    SESSION_COOKIE_SECURE = False
    SECRET_KEY = 'dev-notify-secret-key'
    DANGEROUS_SALT = 'dev-notify-salt'


class Test(Development):
    DEBUG = True
    WTF_CSRF_ENABLED = False


class Preview(Config):
    DEBUG = False
    HTTP_PROTOCOL = 'https'
    HEADER_COLOUR = '#F47738'  # $orange


configs = {
    'development': 'config.Development',
    'test': 'config.Test',
    'preview': 'config.Preview',
    'staging': 'config_staging.Staging',
    'live': 'config_live.Live'
}
