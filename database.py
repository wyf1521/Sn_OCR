"""数据库模块：使用 SQLite 永久保存 OCR 识别结果和用户上传记录"""
import sqlite3
import os
from datetime import datetime
from config import Config


def get_conn():
    os.makedirs(Config.DATA_FOLDER, exist_ok=True)
    conn = sqlite3.connect(Config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """初始化数据库表结构"""
    os.makedirs(Config.DATA_FOLDER, exist_ok=True)
    conn = get_conn()
    cur = conn.cursor()
    # OCR 识别结果表
    cur.execute('''
        CREATE TABLE IF NOT EXISTS ocr_records (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            username        TEXT    NOT NULL,
            image_name      TEXT    NOT NULL,
            image_path      TEXT    NOT NULL,
            full_text       TEXT,
            machine_name    TEXT,
            nic1_name       TEXT,
            nic1_mac        TEXT,
            nic2_name       TEXT,
            nic2_mac        TEXT,
            disk_gb         TEXT,
            memory_gb       TEXT,
            sn_number       TEXT,
            extra_info      TEXT,
            upload_time     TEXT    NOT NULL
        )
    ''')
    # 解析后的结构化字段（来自所有图片，便于 Excel 输出汇总）
    cur.execute('''
        CREATE TABLE IF NOT EXISTS ocr_fields (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id       INTEGER NOT NULL,
            field_name      TEXT    NOT NULL,
            field_value     TEXT,
            FOREIGN KEY (record_id) REFERENCES ocr_records(id) ON DELETE CASCADE
        )
    ''')
    conn.commit()
    conn.close()


def save_record(username, image_name, image_path, parsed: dict, full_text: str):
    """保存一条 OCR 识别记录"""
    conn = get_conn()
    cur = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    extra = parsed.get('_extra', '')

    cur.execute('''
        INSERT INTO ocr_records
        (username, image_name, image_path, full_text,
         machine_name, nic1_name, nic1_mac, nic2_name, nic2_mac,
         disk_gb, memory_gb, sn_number, extra_info, upload_time)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    ''', (
        username, image_name, image_path, full_text,
        parsed.get('machine_name', ''),
        parsed.get('nic1_name', ''),
        parsed.get('nic1_mac', ''),
        parsed.get('nic2_name', ''),
        parsed.get('nic2_mac', ''),
        parsed.get('disk_gb', ''),
        parsed.get('memory_gb', ''),
        parsed.get('sn_number', ''),
        extra,
        now
    ))
    record_id = cur.lastrowid

    # 同时把所有键值对写入 fields 表，方便扩展
    field_rows = [(record_id, k, v) for k, v in parsed.items() if not k.startswith('_')]
    if field_rows:
        cur.executemany(
            'INSERT INTO ocr_fields (record_id, field_name, field_value) VALUES (?,?,?)',
            field_rows
        )

    conn.commit()
    conn.close()
    return record_id


def list_records(username=None, limit=500):
    """查询识别记录，username 不为空时按用户过滤"""
    conn = get_conn()
    cur = conn.cursor()
    if username:
        cur.execute(
            'SELECT * FROM ocr_records WHERE username=? ORDER BY id DESC LIMIT ?',
            (username, limit)
        )
    else:
        cur.execute('SELECT * FROM ocr_records ORDER BY id DESC LIMIT ?', (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_record(record_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('SELECT * FROM ocr_records WHERE id=?', (record_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


# 可编辑的字段白名单（防止任意字段被篡改）
EDITABLE_FIELDS = [
    'machine_name', 'nic1_name', 'nic1_mac',
    'nic2_name', 'nic2_mac', 'disk_gb', 'memory_gb', 'sn_number',
]


def update_record(record_id: int, updates: dict) -> bool:
    """手动更新一条记录的可编辑字段（用于人工核对修正）"""
    # 只允许更新白名单字段
    allowed = {k: v for k, v in updates.items() if k in EDITABLE_FIELDS}
    if not allowed:
        return False

    conn = get_conn()
    cur = conn.cursor()
    set_clause = ', '.join(f'{k}=?' for k in allowed)
    values = list(allowed.values()) + [record_id]
    cur.execute(f'UPDATE ocr_records SET {set_clause} WHERE id=?', values)
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed
