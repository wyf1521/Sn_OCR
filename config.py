import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class Config:
    # 密钥和账号密码只从服务器环境变量读取，不写入仓库。
    SECRET_KEY = os.environ.get('SECRET_KEY') or os.urandom(32)

    # 上传配置
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
    # A folder upload may contain many screenshots. Individual files are
    # still filtered by extension, while the request cap covers the batch.
    MAX_CONTENT_LENGTH = 512 * 1024 * 1024  # 512MB per batch
    MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB per image
    MAX_BATCH_FILES = 500
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'bmp', 'webp'}

    # 数据文件
    DATA_FOLDER = os.path.join(BASE_DIR, 'data')
    DB_PATH = os.path.join(DATA_FOLDER, 'ocr_data.db')
    EXCEL_PATH = os.path.join(DATA_FOLDER, 'ocr_export.xlsx')

    # OCR 供应商配置文件（可随时修改，切换供应商或改 key）
    OCR_CONFIG_PATH = os.path.join(BASE_DIR, 'ocr_config.json')

    # Optional machine-to-machine API authentication.  Keep this separate
    # from the browser password so external callers never need a session.
    EXTERNAL_API_TOKEN = os.environ.get('SN_OCR_API_TOKEN', '').strip()

    # 内部用户账号；部署时设置 SN_OCR_ADMIN_PASSWORD。
    USERS = {
        'admin': os.environ.get('SN_OCR_ADMIN_PASSWORD', ''),
    }

    # Keep browser sessions usable in local development while allowing a
    # stable, operator-managed key in production.  Deployments should always
    # set SECRET_KEY explicitly; the fallback is intentionally ephemeral.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = os.environ.get('SN_OCR_COOKIE_SECURE', '').lower() in {
        '1', 'true', 'yes', 'on'
    }
