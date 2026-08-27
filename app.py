"""Flask 主程序：内部人员 OCR 网页"""
import os
import time
import uuid
import logging
import hmac
from functools import wraps
from flask import (
    Flask, render_template, request, redirect, url_for, session,
    flash, send_from_directory, send_file, jsonify
)
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
from config import Config
import database
import ocr_engine
import excel_export


app = Flask(__name__)
app.config.from_object(Config)
logger = logging.getLogger(__name__)


# ---------- 初始化 ----------
database.init_db()
os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)


# ---------- 辅助 ----------
def allowed_file(filename: str) -> bool:
    if not isinstance(filename, str):
        return False
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in Config.ALLOWED_EXTENSIONS


def _validate_upload(file) -> str:
    """Validate one upload and return its normalized client filename."""
    original_name = (getattr(file, 'filename', '') or '').replace('\\', '/').strip()
    if not original_name:
        raise ValueError('文件名为空')
    if not allowed_file(original_name):
        raise ValueError(f'不支持的文件格式: {original_name}')
    content_length = getattr(file, 'content_length', None)
    if content_length and content_length > Config.MAX_FILE_SIZE:
        raise ValueError(f'单张图片不能超过 {Config.MAX_FILE_SIZE // (1024 * 1024)}MB')
    return original_name


def _image_artifact_paths(image_path: str):
    """Return the original image and all derived cache paths for one image."""
    if not image_path:
        return set()
    basename = os.path.basename(image_path)
    return {
        image_path,
        os.path.join(Config.DATA_FOLDER, 'image_cache', basename),
        os.path.join(Config.DATA_FOLDER, 'image_cache', 'thumbs', basename),
    }


def _remove_image_artifacts(image_path: str):
    """Best-effort cleanup; returns errors instead of masking the main result."""
    errors = []
    for path in _image_artifact_paths(image_path):
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError as exc:
            errors.append(str(exc))
    return errors


def _process_uploaded_file(file, duplicate_policy='skip', overwrite_record_id=None,
                           previous_image_path=None, username=None):
    """Save one uploaded image, run OCR, and persist its structured fields."""
    original_name = _validate_upload(file)

    ext = original_name.rsplit('.', 1)[1].lower()
    new_name = f"{uuid.uuid4().hex}.{ext}"
    save_path = os.path.join(Config.UPLOAD_FOLDER, secure_filename(new_name))
    file.save(save_path)
    if os.path.getsize(save_path) > Config.MAX_FILE_SIZE:
        _remove_image_artifacts(save_path)
        raise ValueError(f'单张图片不能超过 {Config.MAX_FILE_SIZE // (1024 * 1024)}MB')
    provider = ''
    ocr_model = ''
    start = time.time()
    try:
        provider = ocr_engine.get_provider()
        cfg = ocr_engine.load_config()
        ocr_model = cfg.get(provider, {}).get('model', '')
        parsed = ocr_engine.run_ocr(save_path)
        elapsed = round(time.time() - start, 2)
        if not isinstance(parsed, dict):
            full_text = parsed or ''
            parsed = ocr_engine.parse_fields(full_text)
            parsed['_full_text'] = full_text
        full_text = parsed.get('_full_text', '')
        machine_name = str(parsed.get('machine_name', '') or '').strip().upper()
        if machine_name.startswith('YCSPBG'):
            defaults = {
                'brand': 'Lenovo', 'computer_model': '昭阳X5-14IAL',
                'cpu': 'Intel(R) Core(TM) Ultra 5 225H 1.70 GHz',
            }
        elif machine_name.startswith('YCSPGW'):
            defaults = {
                'brand': 'HP', 'computer_model': 'HP Pro Tower 280 G9EPCI',
                'cpu': '13th Gen Intel(R) Core(TM) i3-13100 3.40 GHz',
            }
        else:
            defaults = {}
        defaults.update({
            'system_type': '64位操作系统', 'operating_system': 'Windows 11专业版',
            'version': '24H2',
        })
        for key, value in defaults.items():
            parsed.setdefault(key, value)
        raw_response = parsed.get('_ocr_raw_response', '')
        if raw_response:
            parsed['_raw'] = str(raw_response)[:2000]
        if overwrite_record_id is not None:
            if not database.overwrite_record(
                    overwrite_record_id, username or session.get('user', 'api'), original_name,
                    save_path, parsed, full_text):
                raise ValueError('原记录不存在，无法覆盖')
            old_path = previous_image_path
            if old_path and os.path.abspath(old_path) != os.path.abspath(save_path):
                _remove_image_artifacts(old_path)
            fields = {k: v for k, v in parsed.items() if not k.startswith('_')}
            return {
                'success': True, 'overwritten': True,
                'record_id': overwrite_record_id, 'provider': provider,
                'model': ocr_model, 'elapsed_seconds': elapsed,
                'field_count': len(fields), 'fields': fields,
                'computer_type': excel_export._record_type(parsed),
                'full_text': full_text, 'raw_response': raw_response,
                'image_url': url_for('uploaded_file', filename=new_name),
                'image_name': original_name,
                'upload_time': time.strftime('%Y-%m-%d %H:%M:%S'),
                'message': '已重新识别并覆盖原记录',
            }

        record_id = database.save_record(
            username=username or session.get('user', 'api'), image_name=original_name,
            image_path=save_path, parsed=parsed, full_text=full_text
        )
        if record_id is None:
            duplicate = database.find_record_by_sn(parsed.get('sn_number', ''))
            if duplicate_policy == 'overwrite' and duplicate:
                if not database.overwrite_record(
                        duplicate['id'], username or session.get('user', 'api'), original_name,
                        save_path, parsed, full_text):
                    raise RuntimeError('无法覆盖已有记录')
                old_path = duplicate.get('image_path')
                if old_path and os.path.abspath(old_path) != os.path.abspath(save_path):
                    _remove_image_artifacts(old_path)
                fields = {k: v for k, v in parsed.items() if not k.startswith('_')}
                return {
                    'success': True, 'overwritten': True,
                    'record_id': duplicate['id'], 'provider': provider,
                    'model': ocr_model, 'elapsed_seconds': elapsed,
                    'field_count': len(fields), 'fields': fields,
                    'image_url': url_for('uploaded_file', filename=new_name),
                    'image_name': original_name,
                    'upload_time': time.strftime('%Y-%m-%d %H:%M:%S'),
                    'message': f"SN {parsed.get('sn_number', '')} 已覆盖原记录",
                }
            _remove_image_artifacts(save_path)
            return {
                'success': True, 'duplicate': True,
                'duplicate_record_id': duplicate.get('id') if duplicate else None,
                'provider': provider, 'model': ocr_model,
                'elapsed_seconds': elapsed,
                'image_name': original_name,
                'fields': {k: v for k, v in parsed.items() if not k.startswith('_')},
                'message': f"SN {parsed.get('sn_number', '')} 已上传，跳过重复记录",
            }
        fields = {k: v for k, v in parsed.items() if not k.startswith('_')}
        return {
            'success': True, 'record_id': record_id, 'provider': provider,
            'model': ocr_model, 'elapsed_seconds': elapsed,
            'field_count': len(fields), 'fields': fields,
            'computer_type': excel_export._record_type(parsed),
            'full_text': full_text, 'raw_response': raw_response,
            'image_url': url_for('uploaded_file', filename=new_name),
            'image_name': original_name,
            'upload_time': time.strftime('%Y-%m-%d %H:%M:%S'),
        }
    except Exception:
        _remove_image_artifacts(save_path)
        raise


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapper


def external_api_required(f):
    """Authenticate machine callers with a configured Bearer token."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        configured = Config.EXTERNAL_API_TOKEN
        if not configured:
            return jsonify({
                'success': False,
                'error': '外部 API 未配置，请设置 SN_OCR_API_TOKEN',
            }), 503
        authorization = request.headers.get('Authorization', '')
        scheme, _, token = authorization.partition(' ')
        if scheme.lower() != 'bearer' or not token or not hmac.compare_digest(token, configured):
            return jsonify({'success': False, 'error': 'API Token 无效或缺失'}), 401, {
                'WWW-Authenticate': 'Bearer'
            }
        return f(*args, **kwargs)
    return wrapper


@app.errorhandler(RequestEntityTooLarge)
def request_entity_too_large(_error):
    """Keep oversized batch uploads machine-readable when called via fetch."""
    if request.headers.get('Accept', '').find('application/json') >= 0:
        return jsonify({
            'success': False,
            'error': f'上传请求不能超过 {Config.MAX_CONTENT_LENGTH // (1024 * 1024)}MB',
        }), 413
    flash(f'上传请求不能超过 {Config.MAX_CONTENT_LENGTH // (1024 * 1024)}MB', 'danger')
    return redirect(url_for('index'))


# ---------- 路由 ----------
@app.route('/')
def index():
    if 'user' not in session:
        return redirect(url_for('login'))
    # 所有登录用户共享记录和操作权限
    records = database.list_records(limit=200)
    return render_template('index.html', user=session['user'],
                           records=records)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        expected_password = Config.USERS.get(username, '')
        if expected_password and expected_password == password:
            session['user'] = username
            session.permanent = True
            flash(f'欢迎，{username}', 'success')
            return redirect(url_for('index'))
        flash('账号或密码错误', 'danger')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('login'))


@app.route('/upload', methods=['POST'])
@app.route('/upload-legacy', methods=['POST'])
@login_required
def upload_batch():
    """Process one image or a browser-selected folder of images."""
    want_json = request.headers.get('Accept', '').find('application/json') >= 0
    duplicate_policy = request.form.get('duplicate_policy', 'skip').strip().lower()
    if duplicate_policy not in {'skip', 'overwrite'}:
        duplicate_policy = 'skip'
    files = request.files.getlist('file') + request.files.getlist('files')
    # Accept browser/framework variants such as files[] and unnamed parts.
    if not files:
        for key in request.files.keys():
            files.extend(request.files.getlist(key))
    files = [f for f in files if f and f.filename]
    if not files:
        if want_json:
            return jsonify({'success': False, 'error': '没有收到上传文件，请确认已选择文件夹中的图片文件'}), 400
        flash('没有上传文件', 'danger')
        return redirect(url_for('index'))
    if len(files) > Config.MAX_BATCH_FILES:
        message = f'一次最多上传 {Config.MAX_BATCH_FILES} 张图片'
        if want_json:
            return jsonify({'success': False, 'error': message}), 413
        flash(message, 'danger')
        return redirect(url_for('index'))

    results = []
    for uploaded in files:
        try:
            result = _process_uploaded_file(uploaded, duplicate_policy=duplicate_policy)
            results.append(result)
            logger.info("OCR completed image=%s elapsed=%ss fields=%s duplicate=%s",
                        result['image_name'], result.get('elapsed_seconds', 0),
                        result.get('field_count', 0), result.get('duplicate', False))
        except ValueError as exc:
            results.append({'success': False, 'image_name': uploaded.filename, 'error': str(exc)})
            logger.warning('OCR upload rejected image=%s reason=%s', uploaded.filename, exc)
        except Exception as exc:
            results.append({'success': False, 'image_name': uploaded.filename, 'error': str(exc)})
            logger.exception('OCR failed image=%s', uploaded.filename)

    succeeded = [r for r in results if r.get('success')]
    failed = [r for r in results if not r.get('success')]
    if want_json:
        if len(files) == 1:
            return jsonify(results[0]), 200 if succeeded else 500
        return jsonify({
            'success': bool(succeeded), 'total': len(files),
            'succeeded': len(succeeded), 'failed': len(failed),
            'results': results,
        }), 200 if succeeded else 500
    flash(f'批量识别完成：成功 {len(succeeded)} 张，失败 {len(failed)} 张',
          'success' if succeeded else 'danger')
    return redirect(url_for('index'))


@app.route('/api/v1/ocr', methods=['POST'])
@app.route('/api/v1/upload', methods=['POST'])
@external_api_required
def external_ocr():
    """External OCR interface; unlike the browser route it uses Bearer auth."""
    duplicate_policy = request.form.get('duplicate_policy', 'skip').strip().lower()
    if duplicate_policy not in {'skip', 'overwrite'}:
        duplicate_policy = 'skip'
    files = request.files.getlist('file') + request.files.getlist('files')
    if not files:
        for key in request.files.keys():
            if key.endswith('[]') or key.startswith('file'):
                files.extend(request.files.getlist(key))
    files = [file for file in files if file and file.filename]
    if not files:
        return jsonify({
            'success': False,
            'error': '没有收到上传文件，请使用 multipart/form-data 字段 file',
        }), 400
    if len(files) > Config.MAX_BATCH_FILES:
        return jsonify({
            'success': False,
            'error': f'一次最多上传 {Config.MAX_BATCH_FILES} 张图片',
        }), 413

    results = []
    for uploaded in files:
        try:
            results.append(_process_uploaded_file(
                uploaded, duplicate_policy=duplicate_policy, username='api'
            ))
        except ValueError as exc:
            results.append({
                'success': False, 'image_name': uploaded.filename, 'error': str(exc)
            })
        except Exception as exc:
            logger.exception('External OCR failed image=%s', uploaded.filename)
            results.append({
                'success': False, 'image_name': uploaded.filename,
                'error': 'OCR 处理失败，请查看服务日志',
            })

    succeeded = [result for result in results if result.get('success')]
    if len(results) == 1:
        return jsonify(results[0]), 200 if succeeded else 500
    return jsonify({
        'success': bool(succeeded),
        'total': len(results),
        'succeeded': len(succeeded),
        'failed': len(results) - len(succeeded),
        'results': results,
    }), 200 if succeeded else 500


@app.route('/uploads/<path:filename>')
@login_required
def uploaded_file(filename):
    """返回已上传的图片（供前端预览）"""
    return send_from_directory(Config.UPLOAD_FOLDER, filename)


@app.route('/export')
@login_required
def export():
    """Export records merged into the YCSP inventory template."""
    records = [r for r in database.list_records(limit=1000) if int(r.get('reviewed') or 0) == 1]
    if not records:
        flash('暂无数据可导出', 'warning')
        return redirect(url_for('index'))

    out_dir = os.path.join(Config.DATA_FOLDER, 'exports')
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, 'YCSP电脑信息_已填写.xlsx')
    excel_export.export_to_template(records, output_path=out_path)
    return send_file(out_path, as_attachment=True,
                     download_name='YCSP电脑信息_已填写.xlsx')


@app.route('/export/inventory')
@login_required
def export_inventory():
    """Merge OCR records into the supplied workstation/office workbook."""
    records = [r for r in database.list_records(limit=1000) if int(r.get('reviewed') or 0) == 1]
    if not records:
        flash('暂无数据可填写到电脑信息表', 'warning')
        return redirect(url_for('index'))

    out_dir = os.path.join(Config.DATA_FOLDER, 'exports')
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, 'YCSP电脑信息_已填写.xlsx')
    excel_export.export_to_template(records, output_path=out_path)
    return send_file(out_path, as_attachment=True,
                     download_name='YCSP电脑信息_已填写.xlsx')


@app.route('/api/records')
@login_required
def api_records():
    """JSON API：返回全部记录（便于其他系统对接）"""
    return jsonify(database.list_records(limit=1000))


@app.route('/api/record/<int:record_id>', methods=['PUT'])
@login_required
def update_record(record_id):
    """登录用户手动修改某条记录的可编辑字段"""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'success': False, 'error': '请求体为空'}), 400

    # 过滤掉非字段的内容
    updates = {k: str(v).strip() for k, v in data.items() if isinstance(v, str)}
    if 'reviewed' in data:
        updates['reviewed'] = data['reviewed']

    ok = database.update_record(record_id, updates)
    if not ok:
        return jsonify({'success': False, 'error': '没有可更新的字段或记录不存在'}), 400

    return jsonify({'success': True, 'message': '更新成功', 'record': database.get_record(record_id)})


@app.route('/api/record/<int:record_id>/reprocess', methods=['POST'])
@login_required
def reprocess_record(record_id):
    """Run OCR again for an existing image and replace that record in place."""
    record = database.get_record(record_id)
    if not record:
        return jsonify({'success': False, 'error': '记录不存在'}), 404
    image_path = record.get('image_path') or ''
    if not os.path.isfile(image_path):
        return jsonify({'success': False, 'error': '原图片文件不存在'}), 404
    try:
        from werkzeug.datastructures import FileStorage
        with open(image_path, 'rb') as stream:
            uploaded = FileStorage(
                stream=stream,
                filename=record.get('image_name') or os.path.basename(image_path)
            )
            result = _process_uploaded_file(
                uploaded, overwrite_record_id=record_id,
                previous_image_path=image_path
            )
        return jsonify(result)
    except Exception as exc:
        logger.exception('OCR reprocess failed record=%s', record_id)
        return jsonify({'success': False, 'error': str(exc)}), 500


@app.route('/api/record/<int:record_id>', methods=['DELETE'])
@login_required
def delete_record(record_id):
    """删除记录及其关联的原图、预处理图和导出缩略图。"""
    record = database.get_record(record_id)
    if not record:
        return jsonify({'success': False, 'error': '记录不存在'}), 404

    if not database.delete_record(record_id):
        return jsonify({'success': False, 'error': '删除失败'}), 500

    cleanup_errors = _remove_image_artifacts(record.get('image_path'))

    response = {'success': True, 'record_id': record_id}
    if cleanup_errors:
        response['cleanup_warning'] = '数据库记录已删除，但部分文件清理失败'
    return jsonify(response)


# ---------- 入口 ----------
if __name__ == '__main__':
    # 0.0.0.0 让外部能访问；生产环境请用 gunicorn
    app.run(host='0.0.0.0', port=5000, debug=False)
