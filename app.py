"""Flask 主程序：内部人员 OCR 网页"""
import os
import time
import uuid
from functools import wraps
from flask import (
    Flask, render_template, request, redirect, url_for, session,
    flash, send_from_directory, send_file, jsonify
)
from werkzeug.utils import secure_filename
from config import Config
import database
import ocr_engine
import excel_export


app = Flask(__name__)
app.config.from_object(Config)


# ---------- 初始化 ----------
database.init_db()
os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)


# ---------- 辅助 ----------
def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in Config.ALLOWED_EXTENSIONS


def _process_uploaded_file(file, duplicate_policy='skip', overwrite_record_id=None,
                           previous_image_path=None):
    """Save one uploaded image, run OCR, and persist its structured fields."""
    original_name = (file.filename or '').replace('\\', '/').strip()
    if not original_name:
        raise ValueError('文件名为空')
    if not allowed_file(original_name):
        raise ValueError(f'不支持的文件格式: {original_name}')

    ext = original_name.rsplit('.', 1)[1].lower()
    new_name = f"{uuid.uuid4().hex}.{ext}"
    save_path = os.path.join(Config.UPLOAD_FOLDER, secure_filename(new_name))
    file.save(save_path)
    provider = ocr_engine.get_provider()
    cfg = ocr_engine.load_config()
    ocr_model = cfg.get(provider, {}).get('model', '')
    start = time.time()
    try:
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
                    overwrite_record_id, session['user'], original_name,
                    save_path, parsed, full_text):
                raise ValueError('原记录不存在，无法覆盖')
            old_path = previous_image_path
            if old_path and os.path.abspath(old_path) != os.path.abspath(save_path):
                try:
                    if os.path.isfile(old_path):
                        os.remove(old_path)
                except OSError:
                    pass
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
            username=session['user'], image_name=original_name,
            image_path=save_path, parsed=parsed, full_text=full_text
        )
        if record_id is None:
            duplicate = database.find_record_by_sn(parsed.get('sn_number', ''))
            if duplicate_policy == 'overwrite' and duplicate:
                if not database.overwrite_record(
                        duplicate['id'], session['user'], original_name,
                        save_path, parsed, full_text):
                    raise RuntimeError('无法覆盖已有记录')
                old_path = duplicate.get('image_path')
                if old_path and os.path.abspath(old_path) != os.path.abspath(save_path):
                    try:
                        if os.path.isfile(old_path):
                            os.remove(old_path)
                    except OSError:
                        pass
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
            try:
                if os.path.isfile(save_path):
                    os.remove(save_path)
            except OSError:
                pass
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
        try:
            if os.path.isfile(save_path):
                os.remove(save_path)
        except OSError:
            pass
        raise


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapper


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
        if username in Config.USERS and Config.USERS[username] == password:
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
@login_required
def upload_batch():
    """Process one image or a browser-selected folder of images."""
    want_json = request.headers.get('Accept', '').find('application/json') >= 0
    duplicate_policy = request.form.get('duplicate_policy', 'skip').strip().lower()
    if duplicate_policy not in {'skip', 'overwrite'}:
        duplicate_policy = 'skip'
    files = request.files.getlist('file') or request.files.getlist('files')
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

    results = []
    for uploaded in files:
        try:
            result = _process_uploaded_file(uploaded, duplicate_policy=duplicate_policy)
            results.append(result)
            print(f"[OCR] {result['image_name']} elapsed={result.get('elapsed_seconds', 0)}s "
                  f"fields={result.get('field_count', 0)} duplicate={result.get('duplicate', False)}")
        except Exception as exc:
            results.append({'success': False, 'image_name': uploaded.filename, 'error': str(exc)})
            print(f'[OCR] {uploaded.filename} failed: {exc}')

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


@app.route('/upload-legacy', methods=['POST'])
@login_required
def upload():
    """上传图片并执行 OCR，返回 JSON 结果（供前端在控制台输出）"""
    # 是否希望用 JSON 响应（前端 fetch 请求会带 Accept: application/json）
    want_json = request.headers.get('Accept', '').find('application/json') >= 0

    def _err(message, status=400):
        if want_json:
            return jsonify({'success': False, 'error': message}), status
        flash(message, 'danger')
        return redirect(url_for('index'))

    if 'file' not in request.files:
        return _err('没有上传文件')
    file = request.files['file']
    if file.filename == '':
        return _err('文件名为空')
    if not allowed_file(file.filename):
        return _err('只支持 png/jpg/jpeg/bmp/webp 格式')

    # 保存文件（使用 uuid 避免重名）
    ext = file.filename.rsplit('.', 1)[1].lower()
    new_name = f"{uuid.uuid4().hex}.{ext}"
    save_path = os.path.join(Config.UPLOAD_FOLDER, secure_filename(new_name))
    file.save(save_path)

    # 记录 OCR 调用信息
    provider = ocr_engine.get_provider()
    cfg = ocr_engine.load_config()
    ocr_model = cfg.get(provider, {}).get('model', '')
    start = time.time()

    try:
        parsed = ocr_engine.run_ocr(save_path)
        elapsed = round(time.time() - start, 2)

        # 兜底：万一 run_ocr 返回了字符串，按老逻辑解析
        if not isinstance(parsed, dict):
            full_text = parsed or ''
            parsed = ocr_engine.parse_fields(full_text)
            parsed['_full_text'] = full_text

        full_text = parsed.get('_full_text', '')
        raw_response = parsed.get('_ocr_raw_response', '')

        # 把大模型原始响应也存一份，方便后续排查
        if raw_response:
            parsed['_raw'] = str(raw_response)[:2000]

        record_id = database.save_record(
            username=session['user'],
            image_name=file.filename,
            image_path=save_path,
            parsed=parsed,
            full_text=full_text
        )

        field_count = len([k for k in parsed if not k.startswith('_')])
        result = {
            'success': True,
            'record_id': record_id,
            'provider': provider,
            'model': ocr_model,
            'elapsed_seconds': elapsed,
            'field_count': field_count,
            'fields': {k: v for k, v in parsed.items() if not k.startswith('_')},
            'full_text': full_text,
            'raw_response': raw_response,
            # 供前端刷新历史记录表格使用
            'image_url': url_for('uploaded_file', filename=new_name),
            'image_name': file.filename,
            'upload_time': time.strftime('%Y-%m-%d %H:%M:%S'),
        }
        # 服务端也打印一份 OCR 调用日志
        print(f'[OCR] provider={provider} model={ocr_model} '
              f'耗时={elapsed}s 字段数={field_count} 文本长度={len(full_text)}')

        if want_json:
            return jsonify(result)
        flash(f'识别完成，共提取 {field_count} 个字段', 'success')
        return redirect(url_for('index'))

    except Exception as e:
        elapsed = round(time.time() - start, 2)
        print(f'[OCR] provider={provider} model={ocr_model} 失败 耗时={elapsed}s 错误={e}')
        if want_json:
            return jsonify({
                'success': False,
                'error': str(e),
                'provider': provider,
                'model': ocr_model,
                'elapsed_seconds': elapsed,
            }), 500
        flash(f'识别失败：{e}', 'danger')
        return redirect(url_for('index'))


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
        print(f'[OCR] reprocess record={record_id} failed: {exc}')
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

    paths = {
        record.get('image_path'),
        os.path.join(Config.DATA_FOLDER, 'image_cache', os.path.basename(record.get('image_path', ''))),
        os.path.join(Config.DATA_FOLDER, 'image_cache', 'thumbs', os.path.basename(record.get('image_path', ''))),
    }
    cleanup_errors = []
    for path in paths:
        if not path:
            continue
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError as exc:
            cleanup_errors.append(str(exc))

    response = {'success': True, 'record_id': record_id}
    if cleanup_errors:
        response['cleanup_warning'] = '数据库记录已删除，但部分文件清理失败'
    return jsonify(response)


# ---------- 入口 ----------
if __name__ == '__main__':
    # 0.0.0.0 让外部能访问；生产环境请用 gunicorn
    app.run(host='0.0.0.0', port=5000, debug=False)
