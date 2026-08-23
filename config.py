import os
import json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class Config:
    # 密钥（生产环境请使用环境变量）
    SECRET_KEY = os.environ.get('SECRET_KEY', 'sn-ocr-internal-secret-key-change-me')

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

    # 内部用户账号（实际部署请改为强密码或接入 LDAP/AD）
    USERS = {
        'admin': 'admin@2026',       # 管理员
        'staff': 'staff@2026',       # 普通员工
    }

    # 拥有"编辑/修改数据"权限的管理员名单
    ADMIN_USERS = ['admin']
