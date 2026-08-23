"""Excel 导出模块（支持把原图缩略图嵌入表格）"""
import os
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.drawing.image import Image as XLImage
from PIL import Image as PILImage
from config import Config


COLUMNS = [
    ('ID', 'id', 8),
    ('上传用户', 'username', 14),
    ('机器名', 'machine_name', 18),
    ('网卡1名称', 'nic1_name', 40),
    ('网卡1 MAC', 'nic1_mac', 22),
    ('网卡2名称', 'nic2_name', 40),
    ('网卡2 MAC', 'nic2_mac', 22),
    ('硬盘(GB)', 'disk_gb', 12),
    ('内存(GB)', 'memory_gb', 12),
    ('SN号', 'sn_number', 18),
    ('上传时间', 'upload_time', 22),
    ('图片名', 'image_name', 30),
    ('原图', '_image', 34),   # 特殊列：嵌入图片缩略图
]

# 缩略图缓存目录
THUMB_DIR = os.path.join(Config.DATA_FOLDER, 'image_cache', 'thumbs')
# 缩略图最大尺寸（像素）
THUMB_MAX_W, THUMB_MAX_H = 260, 130
# 数据行行高（磅），1 磅 ≈ 1.333 px，100 磅 ≈ 133px 可容纳缩略图
ROW_HEIGHT_PT = 100


def _make_thumb(image_path: str) -> str:
    """生成（或复用缓存的）缩略图，返回缩略图路径；失败返回 None"""
    if not image_path or not os.path.exists(image_path):
        return None
    os.makedirs(THUMB_DIR, exist_ok=True)
    thumb_path = os.path.join(THUMB_DIR, os.path.basename(image_path))
    if os.path.exists(thumb_path):
        return thumb_path
    try:
        img = PILImage.open(image_path)
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        # 只缩小不放大
        img.thumbnail((THUMB_MAX_W, THUMB_MAX_H), PILImage.LANCZOS)
        img.save(thumb_path, 'JPEG', quality=85)
        return thumb_path
    except Exception:
        return None


def export_to_excel(records: list, output_path: str = None,
                    embed_images: bool = True) -> str:
    """把识别记录导出为 Excel 文件，返回文件路径

    embed_images=True 时，每行末尾嵌入对应上传图片的缩略图。
    """
    if output_path is None:
        output_path = Config.EXCEL_PATH
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    wb = Workbook()
    ws = wb.active
    ws.title = "OCR 识别结果"

    # 表头样式
    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill("solid", fgColor="4472C4")
    header_align = Alignment(horizontal="center", vertical="center")
    thin = Side(border_style="thin", color="BFBFBF")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    # 写表头
    for col_idx, (title, _key, width) in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=1, column=col_idx, value=title)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
        cell.border = border
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    # 写数据
    for row_idx, rec in enumerate(records, start=2):
        # 行高设为容纳缩略图
        ws.row_dimensions[row_idx].height = ROW_HEIGHT_PT

        for col_idx, (_title, key, _w) in enumerate(COLUMNS, start=1):
            # 特殊列：嵌入图片缩略图
            if key == '_image':
                if embed_images:
                    thumb = _make_thumb(rec.get('image_path', ''))
                    if thumb:
                        try:
                            img = XLImage(thumb)
                            img.anchor = f"{get_column_letter(col_idx)}{row_idx}"
                            ws.add_image(img)
                        except Exception:
                            pass  # 单张图片失败不影响整体导出
                continue

            value = rec.get(key, '')
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            cell.border = border

    # 冻结表头
    ws.freeze_panes = 'A2'

    wb.save(output_path)
    return output_path
