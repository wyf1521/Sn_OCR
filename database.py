"""数据库模块：使用 SQLite 永久保存 OCR 识别结果和用户上传记录"""
import re
import sqlite3
import os
from datetime import datetime
from config import Config


def get_conn():
    os.makedirs(Config.DATA_FOLDER, exist_ok=True)
    conn = sqlite3.connect(Config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA busy_timeout = 30000')
    return conn


def init_db():
    """初始化数据库表结构"""
    os.makedirs(Config.DATA_FOLDER, exist_ok=True)
    conn = get_conn()
    cur = conn.cursor()
    # WAL is persistent database state; configure it once during startup
    # instead of paying the mode-switch cost on every read connection.
    cur.execute('PRAGMA journal_mode = WAL')
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
    # Fields introduced after the first database version. SQLite keeps
    # existing installations compatible through additive migrations.
    existing = {row[1] for row in cur.execute('PRAGMA table_info(ocr_records)').fetchall()}
    for name in ('brand', 'computer_model', 'cpu', 'system_type',
                 'operating_system', 'version', 'department', 'person_name',
                 'employee_id', 'install_time', 'migrate_domain', 'reviewed'):
        if name not in existing:
            if name == 'reviewed':
                cur.execute('ALTER TABLE ocr_records ADD COLUMN reviewed INTEGER NOT NULL DEFAULT 0')
            elif name == 'migrate_domain':
                cur.execute("ALTER TABLE ocr_records ADD COLUMN migrate_domain TEXT NOT NULL DEFAULT '是'")
            else:
                cur.execute(f'ALTER TABLE ocr_records ADD COLUMN {name} TEXT')
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
    # These indexes cover the duplicate check and the common UI/export query
    # paths without changing the existing schema or stored values.
    cur.execute('''
        CREATE INDEX IF NOT EXISTS idx_ocr_records_sn_normalized
        ON ocr_records (lower(trim(sn_number)))
    ''')
    cur.execute('''
        CREATE INDEX IF NOT EXISTS idx_ocr_records_upload_time
        ON ocr_records (upload_time DESC)
    ''')
    cur.execute('''
        CREATE INDEX IF NOT EXISTS idx_ocr_records_reviewed
        ON ocr_records (reviewed)
    ''')
    cur.execute('''
        CREATE INDEX IF NOT EXISTS idx_ocr_fields_record
        ON ocr_fields (record_id)
    ''')
    conn.commit()
    conn.close()


def save_record(username, image_name, image_path, parsed: dict, full_text: str):
    """保存一条 OCR 识别记录"""
    conn = get_conn()
    cur = conn.cursor()
    # Serialize the check-and-insert pair so concurrent folder uploads cannot
    # both pass the duplicate-SN check.
    conn.execute('BEGIN IMMEDIATE')
    sn_number = str(parsed.get('sn_number', '') or '').strip()
    if sn_number:
        cur.execute('SELECT id FROM ocr_records WHERE lower(trim(sn_number))=lower(?) LIMIT 1', (sn_number,))
        duplicate = cur.fetchone()
        if duplicate:
            conn.close()
            return None
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    extra = parsed.get('_extra', '')

    cur.execute('''
        INSERT INTO ocr_records
        (username, image_name, image_path, full_text,
         machine_name, nic1_name, nic1_mac, nic2_name, nic2_mac,
         disk_gb, memory_gb, sn_number, extra_info, upload_time,
         brand, computer_model, cpu, system_type, operating_system, version,
         department, person_name, employee_id, install_time, migrate_domain)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
        ,parsed.get('brand', ''), parsed.get('computer_model', ''), parsed.get('cpu', ''),
        parsed.get('system_type', ''), parsed.get('operating_system', ''), parsed.get('version', ''),
        parsed.get('department', ''), parsed.get('person_name', ''), parsed.get('employee_id', ''),
        parsed.get('install_time', ''), parsed.get('migrate_domain', '是') or '是'
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


def overwrite_record(record_id: int, username, image_name, image_path,
                     parsed: dict, full_text: str) -> bool:
    """Replace an existing OCR record and its structured field snapshot."""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('''
        UPDATE ocr_records SET
            username=?, image_name=?, image_path=?, full_text=?,
            machine_name=?, nic1_name=?, nic1_mac=?, nic2_name=?, nic2_mac=?,
            disk_gb=?, memory_gb=?, sn_number=?, extra_info=?, upload_time=?,
            brand=?, computer_model=?, cpu=?, system_type=?, operating_system=?, version=?,
            department=?, person_name=?, employee_id=?, install_time=?, migrate_domain=?
            , reviewed=0
        WHERE id=?
    ''', (
        username, image_name, image_path, full_text,
        parsed.get('machine_name', ''), parsed.get('nic1_name', ''), parsed.get('nic1_mac', ''),
        parsed.get('nic2_name', ''), parsed.get('nic2_mac', ''), parsed.get('disk_gb', ''),
        parsed.get('memory_gb', ''), parsed.get('sn_number', ''), parsed.get('_extra', ''), now,
        parsed.get('brand', ''), parsed.get('computer_model', ''), parsed.get('cpu', ''),
        parsed.get('system_type', ''), parsed.get('operating_system', ''), parsed.get('version', ''),
        parsed.get('department', ''), parsed.get('person_name', ''), parsed.get('employee_id', ''),
        parsed.get('install_time', ''), parsed.get('migrate_domain', '是') or '是', record_id
    ))
    changed = cur.rowcount > 0
    if changed:
        cur.execute('DELETE FROM ocr_fields WHERE record_id=?', (record_id,))
        field_rows = [(record_id, k, v) for k, v in parsed.items() if not k.startswith('_')]
        if field_rows:
            cur.executemany(
                'INSERT INTO ocr_fields (record_id, field_name, field_value) VALUES (?,?,?)',
                field_rows
            )
        conn.commit()
    else:
        conn.rollback()
    conn.close()
    return changed


def find_record_by_sn(sn_number: str):
    sn_number = str(sn_number or '').strip()
    if not sn_number:
        return None
    conn = get_conn()
    row = conn.execute(
        'SELECT * FROM ocr_records WHERE lower(trim(sn_number))=lower(?) LIMIT 1',
        (sn_number,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def find_record_by_machine_name(machine_name):
    """Find one record by its normalized computer name."""
    normalized = str(machine_name or '').strip()
    if not normalized:
        return None
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        'SELECT * FROM ocr_records WHERE upper(trim(machine_name))=upper(?) LIMIT 1',
        (normalized,)
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None

def list_records(username=None, limit=500):
    """查询识别记录，username 不为空时按用户过滤"""
    try:
        limit = max(1, min(int(limit), 5000))
    except (TypeError, ValueError):
        limit = 500
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


PAGE_SIZES = (10, 20, 40)
_PAGE_FIELDS = (
    "id", "image_name", "image_path", "machine_name", "sn_number",
    "nic1_name", "nic1_mac", "nic2_name", "nic2_mac", "disk_gb", "memory_gb",
    "department", "person_name", "employee_id", "upload_time", "migrate_domain",
    "reviewed",
)
_SEARCH_COLUMNS = (
    "image_name", "machine_name", "sn_number", "nic1_name", "nic1_mac",
    "nic2_name", "nic2_mac", "disk_gb", "memory_gb", "department",
    "person_name", "employee_id", "upload_time", "migrate_domain",
)
_SORT_COLUMNS = {
    "reviewed": "reviewed",
    "machine_name": "machine_name",
    "sn_number": "sn_number",
    "nic1_name": "nic1_name",
    "nic1_mac": "nic1_mac",
    "nic2_name": "nic2_name",
    "nic2_mac": "nic2_mac",
    "disk_gb": "disk_gb",
    "memory_gb": "memory_gb",
    "department": "department",
    "person_name": "person_name",
    "employee_id": "employee_id",
    "upload_time": "upload_time",
    "migrate_domain": "migrate_domain",
}


def _machine_sort_key(name):
    text = str(name or "").strip().upper()
    if not text:
        return (1, ())
    parts = re.split(r"(\d+)", text)
    natural = tuple((1, int(part)) if part.isdigit() else (0, part) for part in parts if part)
    return (0, natural)


def _page_record(row):
    return {key: row[key] for key in _PAGE_FIELDS}


def query_records(page=1, page_size=10, query="", computer_type="all",
                  review="all", sort="", order="desc"):
    """Return one page of records. Images are not included; only the current page is returned."""
    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(page_size)
    except (TypeError, ValueError):
        page_size = 10
    if page_size not in PAGE_SIZES:
        page_size = 10
    page = max(1, page)
    filters = []
    params = []
    kind = str(computer_type or "all").strip().lower()
    if kind == "workstation":
        filters.append("upper(trim(COALESCE(machine_name, ''))) LIKE 'YCSPGW%'")
    elif kind == "office":
        filters.append("upper(trim(COALESCE(machine_name, ''))) LIKE 'YCSPBG%'")
    review_mode = str(review or "all").strip().lower()
    if review_mode == "reviewed":
        filters.append("COALESCE(reviewed, 0) = 1")
    elif review_mode == "unreviewed":
        filters.append("COALESCE(reviewed, 0) = 0")
    text = str(query or "").strip()
    if text:
        escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped.upper()}%"
        filters.append("(" + " OR ".join(
            f"upper(COALESCE({column}, '')) LIKE ? ESCAPE '\\'" for column in _SEARCH_COLUMNS
        ) + ")")
        params.extend([like] * len(_SEARCH_COLUMNS))
    where_sql = (" WHERE " + " AND ".join(filters)) if filters else ""
    direction = "ASC" if str(order or "").strip().lower() == "asc" else "DESC"
    sort_key = str(sort or "").strip()
    sort_column = _SORT_COLUMNS.get(sort_key, "")
    conn = get_conn()
    total = conn.execute(f"SELECT COUNT(*) FROM ocr_records{where_sql}", params).fetchone()[0]
    pages = max(1, (total + page_size - 1) // page_size) if total else 1
    if page > pages:
        page = pages
    offset = (page - 1) * page_size
    columns = ", ".join(_PAGE_FIELDS)
    if sort_key == "machine_name":
        id_rows = conn.execute(
            f"SELECT id, machine_name FROM ocr_records{where_sql}",
            params,
        ).fetchall()
        id_rows = sorted(id_rows, key=lambda row: _machine_sort_key(row["machine_name"]), reverse=direction == "DESC")
        page_ids = [row["id"] for row in id_rows[offset:offset + page_size]]
        if page_ids:
            placeholders = ", ".join("?" for _ in page_ids)
            fetched = conn.execute(
                f"SELECT {columns} FROM ocr_records WHERE id IN ({placeholders})",
                page_ids,
            ).fetchall()
            by_id = {row["id"]: _page_record(row) for row in fetched}
            records = [by_id[record_id] for record_id in page_ids if record_id in by_id]
        else:
            records = []
    else:
        if sort_column in {"disk_gb", "memory_gb"}:
            order_sql = f"CAST(NULLIF({sort_column}, '') AS REAL) {direction}, id DESC"
        elif sort_column:
            order_sql = f"{sort_column} {direction}, id DESC"
        else:
            order_sql = "id DESC"
        rows = conn.execute(
            f"SELECT {columns} FROM ocr_records{where_sql} ORDER BY {order_sql} LIMIT ? OFFSET ?",
            params + [page_size, offset],
        ).fetchall()
        records = [_page_record(row) for row in rows]
    conn.close()
    return {
        "records": records,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }


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
    'department', 'person_name', 'employee_id', 'migrate_domain', 'reviewed',
]


def update_record(record_id: int, updates: dict) -> bool:
    """手动更新一条记录的可编辑字段（用于人工核对修正）"""
    # 只允许更新白名单字段
    allowed = {k: v for k, v in updates.items() if k in EDITABLE_FIELDS}
    if 'reviewed' in allowed:
        value = allowed['reviewed']
        if isinstance(value, str):
            allowed['reviewed'] = 1 if value.strip().lower() in {'1', 'true', 'yes', 'on', '已审核'} else 0
        else:
            allowed['reviewed'] = 1 if value else 0
    if not allowed:
        return False

    conn = get_conn()
    cur = conn.cursor()
    set_clause = ', '.join(f'{k}=?' for k in allowed)
    values = list(allowed.values()) + [record_id]
    cur.execute(f'UPDATE ocr_records SET {set_clause} WHERE id=?', values)
    changed = cur.rowcount > 0
    if changed:
        for field_name, field_value in allowed.items():
            cur.execute('''
                UPDATE ocr_fields SET field_value=?
                WHERE record_id=? AND field_name=?
            ''', (field_value, record_id, field_name))
            if cur.rowcount == 0:
                cur.execute(
                    'INSERT INTO ocr_fields (record_id, field_name, field_value) VALUES (?,?,?)',
                    (record_id, field_name, field_value)
                )
    conn.commit()
    conn.close()
    return changed


def delete_record(record_id: int) -> bool:
    """删除一条 OCR 记录；关联字段由外键级联删除。"""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute('DELETE FROM ocr_records WHERE id=?', (record_id,))
        deleted = cur.rowcount > 0
        conn.commit()
        return deleted
    finally:
        conn.close()
