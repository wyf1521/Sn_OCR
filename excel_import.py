"""Read computer inventory records from exported Excel workbooks."""
from datetime import date, datetime
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook

import excel_export

MAX_WORKBOOK_SIZE = 50 * 1024 * 1024
MAX_UNCOMPRESSED_SIZE = 250 * 1024 * 1024
MAX_IMPORT_ROWS = 5000

HEADER_TO_FIELD = {
    str(title).strip().casefold(): key
    for title, key in excel_export.OFFICE_HEADERS
    if key
}
HEADER_TO_FIELD.update({
    "\u662f\u5426\u8fc1\u57df": "migrate_domain",
    "\u8fc1\u57df": "migrate_domain",
    "\u5ba1\u6838\u72b6\u6001": "reviewed",
})


def _cell_value(value):
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d %H:%M:%S") if isinstance(value, datetime) else value.isoformat()
    return value


def _workbook_images(sheet):
    images_by_row = {}
    for image in getattr(sheet, "_images", []):
        anchor = getattr(image, "anchor", None)
        marker = getattr(anchor, "_from", None)
        if marker is None:
            continue
        try:
            image_data = image._data()
        except Exception:
            continue
        image_format = str(getattr(image, "format", "")).lower()
        if image_format not in {"png", "jpeg"} or not image_data:
            continue
        images_by_row.setdefault(marker.row + 1, (image_format, image_data))
    return images_by_row


def read_excel_records(file_storage):
    """Return normalized rows from one or more inventory sheets.

    Each result contains ``fields``, optional ``image_format`` and ``image_data``.
    """
    filename = (getattr(file_storage, "filename", "") or "").strip()
    if not filename.lower().endswith(".xlsx"):
        raise ValueError("只支持导入 .xlsx 格式的 Excel 文件")

    stream = file_storage.stream
    stream.seek(0, 2)
    file_size = stream.tell()
    stream.seek(0)
    if file_size <= 0 or file_size > MAX_WORKBOOK_SIZE:
        raise ValueError("Excel 文件为空或超过 50 MB")
    try:
        with ZipFile(stream) as archive:
            if sum(item.file_size for item in archive.infolist()) > MAX_UNCOMPRESSED_SIZE:
                raise ValueError("Excel 文件解压后超过 250 MB，已拒绝导入")
        stream.seek(0)
        workbook = load_workbook(stream, data_only=True)
    except BadZipFile as exc:
        raise ValueError("文件不是有效的 .xlsx 工作簿") from exc

    imported = []
    try:
        for sheet in workbook.worksheets:
            header_columns = {}
            for cell in sheet[1]:
                if cell.value is None:
                    continue
                key = HEADER_TO_FIELD.get(str(cell.value).strip().casefold())
                if key:
                    header_columns[cell.column] = key
            if "machine_name" not in header_columns.values():
                continue

            images_by_row = _workbook_images(sheet)
            for row_number in range(2, sheet.max_row + 1):
                fields = {}
                for column, key in header_columns.items():
                    value = _cell_value(sheet.cell(row_number, column).value)
                    if value is None:
                        continue
                    if isinstance(value, str):
                        value = value.strip()
                    fields[key] = value
                machine_name = str(fields.get("machine_name", "") or "").strip()
                sn_number = str(fields.get("sn_number", "") or "").strip()
                if not machine_name or not sn_number:
                    continue
                fields["sn_number"] = sn_number
                image = images_by_row.get(row_number)
                has_details = any(
                    key != "machine_name" and value not in (None, "")
                    for key, value in fields.items()
                )
                if not has_details and not image:
                    continue
                fields["machine_name"] = machine_name
                fields.pop("reviewed", None)
                imported.append({
                    "fields": fields,
                    "image": image,
                    "sheet": sheet.title,
                    "row": row_number,
                })
                if len(imported) > MAX_IMPORT_ROWS:
                    raise ValueError(f"单个工作簿最多导入 {MAX_IMPORT_ROWS} 条记录")
    finally:
        workbook.close()

    if not imported:
        raise ValueError("未找到包含“计算机名”列的有效工作表")
    return imported
