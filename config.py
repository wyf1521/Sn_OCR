import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class Config:
    # 密钥和账号密码只从服务器环境变量读取，不写入仓库。
    SECRET_KEY = os.environ.get('SECRET_KEY') or os.urandom(32)

    # 上传配置
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'bmp', 'webp'}

    # 数据文件
    DATA_FOLDER = os.path.join(BASE_DIR, 'data')
    DB_PATH = os.path.join(DATA_FOLDER, 'ocr_data.db')
    EXCEL_PATH = os.path.join(DATA_FOLDER, 'ocr_export.xlsx')

    # OCR 供应商配置文件（可随时修改，切换供应商或改 key）
    OCR_CONFIG_PATH = os.path.join(BASE_DIR, 'ocr_config.json')

    # 内部用户账号；部署时设置 SN_OCR_ADMIN_PASSWORD。
    USERS = {
        'admin': os.environ.get('SN_OCR_ADMIN_PASSWORD', ''),
    }
