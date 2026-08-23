"""OCR 引擎模块：支持多供应商，针对 Windows Script Host 计算机信息优化

当前支持：
  - siliconflow : 大模型 OCR API（DeepSeek-OCR 等），通过 OpenAI 兼容接口
  - paddle      : 本地 PaddleOCR（离线，无需 API key）

切换方式：修改 ocr_config.json 中的 "provider" 字段即可。
"""
import re
import os
import io
import json
import base64
import sys
import urllib.request
import urllib.error
from PIL import Image

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, 'ocr_config.json')


def load_config() -> dict:
    """加载 OCR 供应商配置"""
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def get_provider() -> str:
    """返回当前选用的 OCR 供应商名称"""
    cfg = load_config()
    return cfg.get('provider', 'siliconflow')


# ---------------- 图片预处理 ----------------

def preprocess_image(image_path: str) -> str:
    """对上传的图片做轻度预处理，返回处理后的路径

    策略：
      - 过大图片先缩放到最长边 1600px，节省 OCR 时间和费用
      - 提升对比度 / 转灰度（让弹窗文字更清晰）
    处理后保存到 uploads 旁的 cache 目录。
    """
    cache_dir = os.path.join(BASE_DIR, 'data', 'image_cache')
    os.makedirs(cache_dir, exist_ok=True)
    out_path = os.path.join(cache_dir, os.path.basename(image_path))

    img = Image.open(image_path)
    if img.mode != 'RGB':
        img = img.convert('RGB')

    # 等比缩放，最长边 <= 1600
    max_side = 1600
    w, h = img.size
    if max(w, h) > max_side:
        scale = max_side / max(w, h)
        new_size = (int(w * scale), int(h * scale))
        img = img.resize(new_size, Image.LANCZOS)

    img.save(out_path, 'JPEG', quality=92)
    return out_path


# ---------------- 大模型 OCR API 供应商 ----------------

# DeepSeek-OCR 等「专用 OCR 模型」不认识复杂 system prompt，
# 官方用法是把极简指令（如 "OCR:"）和图片一起放在 user 消息里。
# 默认指令可在 ocr_config.json 的 "prompt" 字段覆盖；
# 通用视觉模型（Qwen-VL 等）可配置 "system_prompt" 使用复杂指令。
DEFAULT_OCR_PROMPT = "OCR:"


def _encode_image(image_path: str) -> str:
    """把图片转成 base64 data URL"""
    with open(image_path, 'rb') as f:
        data = f.read()
    ext = os.path.splitext(image_path)[1].lower().lstrip('.')
    mime = {
        'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
        'bmp': 'image/bmp', 'webp': 'image/webp',
    }.get(ext, 'image/jpeg')
    b64 = base64.b64encode(data).decode('utf-8')
    return f"data:{mime};base64,{b64}"


def _call_openai_vision(cfg: dict, image_path: str) -> str:
    """通过 OpenAI 兼容接口调用视觉大模型做 OCR

    适用供应商：SiliconFlow / DeepSeek / 通义千问兼容模式 / 智谱 等。
    更换供应商时只需修改 ocr_config.json 中的 api_base / api_key / model。

    消息结构（对齐官方网页版的调用方式）：
      - user 消息 = [图片, 文本指令]   ← 指令必须跟图片放一起
      - system 消息 = 可选（仅当配置了 system_prompt 才发送）
    """
    api_base = cfg['api_base'].rstrip('/')
    api_key = cfg.get('api_key', '')
    model = cfg.get('model', 'deepseek-ai/DeepSeek-OCR')
    timeout = cfg.get('timeout', 120)
    max_tokens = cfg.get('max_tokens', 4096)
    # 采样参数（对齐官方网页默认值）
    temperature = cfg.get('temperature', 0.0)
    top_p = cfg.get('top_p', 0.7)
    # OCR 指令：默认极简 "OCR:"（DeepSeek-OCR 官方用法），可在配置中覆盖
    ocr_prompt = cfg.get('prompt', DEFAULT_OCR_PROMPT)
    # 可选的 system 消息（通用视觉模型才需要，专用 OCR 模型不要配）
    system_prompt = cfg.get('system_prompt', '')

    if not api_key or api_key.startswith('请'):
        raise RuntimeError('OCR API key 未配置，请编辑 ocr_config.json 填写 api_key')

    url = f"{api_base}/chat/completions"
    data_url = _encode_image(image_path)

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": data_url}},
            {"type": "text", "text": ocr_prompt},
        ],
    })

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
    }

    # top_k 并非所有 OpenAI 兼容供应商都支持，只在显式配置时才发送
    if cfg.get('top_k') is not None:
        payload['top_k'] = cfg['top_k']

    # 只有当配置中显式指定时才开启 JSON 模式（默认关闭）
    if cfg.get('response_format'):
        payload['response_format'] = {'type': cfg['response_format']}

    body = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
        },
        method='POST',
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', errors='ignore')
        raise RuntimeError(f'OCR API 请求失败 (HTTP {e.code}): {detail}')
    except urllib.error.URLError as e:
        raise RuntimeError(f'OCR API 网络错误: {e.reason}')

    try:
        content = result['choices'][0]['message']['content']
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f'OCR API 返回格式异常: {result}')

    if not content or not content.strip():
        raise RuntimeError('OCR API 返回了空内容')

    return content.strip()


def _extract_json(text: str) -> dict:
    """从大模型返回里抽出结构化字段，兼容 JSON / Markdown 表格 / 键值行"""
    if not text:
        return {}

    # 尝试 1：直接解析 JSON
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    # 尝试 2：去掉 markdown 代码块标记再解析
    cleaned = re.sub(r'```(?:json)?\s*', '', text)
    cleaned = cleaned.replace('```', '').strip()
    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    # 尝试 3：用正则抠出第一个 {...} 块
    m = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass

    # 尝试 4：解析 Markdown 表格（| 项目 | 数据 |）
    table = _parse_markdown_table(text)
    if table:
        return table

    # 尝试 5：解析 HTML 表格（<table>...</table>）
    table = _parse_html_table(text)
    if table:
        return table

    # 尝试 6：解析 "键：值" 或 "键: 值" 键值行
    kv = _parse_key_value_lines(text)
    if kv:
        return kv

    return {}


def _parse_markdown_table(text: str) -> dict:
    """解析 Markdown 表格，把每行的「项目 | 数据」变成 key-value

    处理类似：
        | 项目 | 数据 |
        | 机器名 | YCSPBG000095 |
        | 网卡1 | [00000010] Intel(R) ... |
        | 物理地址 | 80:CA:52:C8:1A:21 |
    其中「物理地址」会归属于它上面最近的「网卡」。
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip().startswith('|')]
    if not lines:
        return {}

    rows = []
    for ln in lines:
        cells = [c.strip() for c in ln.strip('|').split('|')]
        # 跳过表格分隔行（如 |---|---|）
        if all(re.fullmatch(r':?-{2,}:?', c or '-') for c in cells):
            continue
        rows.append(cells)

    result = {}
    last_key = None
    for cells in rows:
        if len(cells) < 2:
            continue
        k = cells[0].strip()
        v = cells[1].strip()
        if not k or not v:
            continue
        # 表头行（项目/字段名等）跳过
        if k in ('项目', '字段', '字段名', '名称', '属性', '参数') or '---' in k:
            continue
        # 映射为英文 key
        norm_key = _map_field_key(k)
        if norm_key == 'physical_address':
            # 「物理地址」归属最近的网卡：nic1_name -> nic1_mac
            if last_key and last_key.startswith('nic'):
                base = re.sub(r'_name$', '', last_key)
                result[base + '_mac'] = v
            continue
        result[norm_key] = v
        last_key = norm_key

    return result


def _parse_html_table(text: str) -> dict:
    """解析 HTML 表格，把每行 <tr> 的前两个单元格变成 key-value

    处理类似：
        <table><tr><th>项目</th><th>数据</th></tr>
        <tr><td>机器名</td><td>YCSPBG000095</td></tr>
        <tr><td>网卡1</td><td>...</td></tr>
        <tr><td>物理地址</td><td>80:CA:52:C8:1A:21</td></tr>
        </table>
    """
    if '<table' not in text.lower():
        return {}

    # 去掉所有 HTML 标签之间的空白，方便逐行处理
    rows_html = re.findall(r'<tr[^>]*>(.*?)</tr>', text, re.S | re.I)
    if not rows_html:
        return {}

    result = {}
    last_key = None
    for row_html in rows_html:
        # 提取该行的所有单元格（td 或 th）
        cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row_html, re.S | re.I)
        if len(cells) < 2:
            continue
        k = _strip_html(cells[0]).strip()
        v = _strip_html(cells[1]).strip()
        if not k or not v:
            continue
        # 表头行跳过
        if k in ('项目', '字段', '字段名', '名称', '属性', '参数'):
            continue
        norm_key = _map_field_key(k)
        if norm_key == 'physical_address':
            if last_key and last_key.startswith('nic'):
                base = re.sub(r'_name$', '', last_key)
                result[base + '_mac'] = v
            continue
        result[norm_key] = v
        last_key = norm_key

    return result


def _strip_html(s: str) -> str:
    """去除字符串里的 HTML 标签，返回纯文本"""
    s = re.sub(r'<[^>]+>', '', s)
    # 还原常见 HTML 实体
    s = (s.replace('&nbsp;', ' ').replace('&amp;', '&')
          .replace('&lt;', '<').replace('&gt;', '>')
          .replace('&quot;', '"').replace('&#39;', "'"))
    return s


def _parse_key_value_lines(text: str) -> dict:
    """解析纯文本键值行（机器名：xxx / 网卡名称：xxx / 物理地址：xxx）

    网卡是块状结构（「网卡N：」/「网卡名称：」/「物理地址：」三行一组），
    用 current_nic 跟踪当前正在填充的网卡编号，避免相互覆盖错位。
    """
    result = {}
    current_nic = 0  # 当前网卡编号（0 = 还没开始）

    for ln in text.splitlines():
        ln = ln.strip()
        m = re.match(r'^(.+?)[：:]\s*(.*)$', ln)
        if not m:
            continue
        k = m.group(1).strip()
        v = m.group(2).strip()

        # 「网卡1：」「网卡2：」行（值通常为空，仅推进编号）
        if re.fullmatch(r'(?:网卡|网络)\s*[12]', k):
            current_nic = int(k[-1])
            if v:
                result[f'nic{current_nic}_name'] = v
            continue

        # 「网卡名称：xxx」（无编号时按出现顺序分配网卡）
        if re.fullmatch(r'网卡名称', k) and v:
            if current_nic == 0:
                current_nic = 1
            elif f'nic{current_nic}_name' in result:
                current_nic = min(current_nic + 1, 2)
            result[f'nic{current_nic}_name'] = v
            continue

        # 「物理地址：xxx」归属当前网卡
        if re.fullmatch(r'物理地址', k) and v:
            if current_nic > 0:
                result[f'nic{current_nic}_mac'] = v
            continue

        # 其他键值（机器名 / 硬盘 / 内存 / SN 等）
        norm_key = _map_field_key(k)
        if norm_key and v:
            result[norm_key] = v

    return result


def _map_field_key(k: str) -> str:
    """把中文/各种写法的字段名映射为标准英文 key"""
    k = k.strip().lower()
    # 物理地址（MAC）
    if any(s in k for s in ('物理地址', 'mac', '物理')):
        return 'physical_address'
    # 机器名
    if any(s in k for s in ('机器名', '主机名', '计算机名', 'hostname', 'host')):
        return 'machine_name'
    # 网卡
    if re.search(r'网卡\s*1|网卡一|网卡1|nic\s*1|nic1', k):
        return 'nic1_name'
    if re.search(r'网卡\s*2|网卡二|网卡2|nic\s*2|nic2|网络2', k):
        return 'nic2_name'
    if re.search(r'网卡|nic', k):
        return 'nic1_name'
    # 硬盘
    if any(s in k for s in ('硬盘', '磁盘', 'disk', 'hdd', 'ssd')):
        return 'disk_gb'
    # 内存
    if any(s in k for s in ('内存', 'memory', 'ram')):
        return 'memory_gb'
    # SN 号
    if any(s in k for s in ('sn', '序列号', 'serial')):
        return 'sn_number'
    return ''


def _normalize_fields(raw: dict) -> dict:
    """把大模型返回的 JSON 清洗成标准结构化字段"""
    norm = {
        'machine_name': '',
        'nic1_name': '',
        'nic1_mac': '',
        'nic2_name': '',
        'nic2_mac': '',
        'disk_gb': '',
        'memory_gb': '',
        'sn_number': '',
        '_raw': json.dumps(raw, ensure_ascii=False),  # 原样保留，便于排查
    }
    if not isinstance(raw, dict):
        return norm

    def first(*keys):
        for k in keys:
            v = raw.get(k)
            if v and isinstance(v, str):
                return v.strip()
        return ''

    # 以下字段【不做任何二次加工】，识别到什么就保留什么，
    # 由管理员在页面上人工核对和修正。
    norm['machine_name'] = first('machine_name', 'machinename', '机器名', 'hostname', 'host_name')
    norm['nic1_name'] = first('nic1_name', 'nic1', 'nic_1_name', '网卡1名称', '网卡1', '网卡 1 名称')
    norm['nic1_mac'] = first('nic1_mac', 'mac1', 'nic_1_mac', '网卡1物理地址', '网卡1 MAC', '网卡1MAC')
    norm['nic2_name'] = first('nic2_name', 'nic2', 'nic_2_name', '网卡2名称', '网卡2', '网卡 2 名称')
    norm['nic2_mac'] = first('nic2_mac', 'mac2', 'nic_2_mac', '网卡2物理地址', '网卡2 MAC', '网卡2MAC')
    norm['disk_gb'] = first('disk_gb', 'disk', '硬盘', '硬盘(GB)', 'disk_gb_value', 'diskGB')
    norm['memory_gb'] = first('memory_gb', 'memory', '内存', '内存(GB)', 'ram', 'ram_gb')
    norm['sn_number'] = first('sn_number', 'sn', 'SN', 'sn号', 'serial_number', 'serial')

    return norm


# ---------------- 本地 PaddleOCR 供应商 ----------------

_paddle_instance = None


def _get_paddle():
    global _paddle_instance
    if _paddle_instance is None:
        try:
            from paddleocr import PaddleOCR
        except ImportError:
            raise RuntimeError('未安装 paddleocr，请 pip install paddleocr')
        lang = load_config().get('paddle', {}).get('lang', 'ch')
        _paddle_instance = PaddleOCR(lang=lang)
    return _paddle_instance


def _run_paddle(image_path: str) -> str:
    """本地 PaddleOCR 识别（离线兜底）"""
    ocr = _get_paddle()
    old_stdout, old_stderr = sys.stdout, sys.stderr
    try:
        sys.stdout = open('NUL', 'w') if sys.platform == 'win32' else open('/dev/null', 'w')
        sys.stderr = sys.stdout
        result = ocr.predict(image_path)
    finally:
        sys.stdout.close()
        sys.stdout, sys.stderr = old_stdout, old_stderr

    lines = []
    if not result:
        return ''
    for item in result:
        texts = None
        if hasattr(item, 'rec_texts'):
            texts = item.rec_texts
        elif isinstance(item, dict):
            texts = item.get('rec_texts') or item.get('texts')
        if texts is None:
            try:
                texts = [t for t in item if isinstance(t, str)]
            except Exception:
                texts = []
        for t in texts:
            if t and str(t).strip():
                lines.append(str(t).strip())
    return '\n'.join(lines)


# ---------------- 统一入口 ----------------

def run_ocr(image_path: str) -> dict:
    """根据配置的 provider 执行 OCR，返回结构化字段 dict

    流程（两阶段）：
      1. 大模型/本地 OCR 识别出原始文本（与官方网页版风格一致）
      2. 代码从原始文本里提取结构化字段：先尝试 JSON/Markdown/HTML 解析，再走正则兜底

    返回 dict 形如：
        {
            'machine_name': '...', 'nic1_name': '...', 'nic1_mac': '...',
            'nic2_name': '...', 'nic2_mac': '...',
            'disk_gb': '...', 'memory_gb': '...', 'sn_number': '...',
            '_full_text': '...原始识别文本...',
            '_ocr_raw_response': '...',  # 大模型原始返回
        }
    """
    processed = preprocess_image(image_path)
    provider = get_provider()
    cfg = load_config()

    # 1. 拿到原始识别文本
    if provider == 'siliconflow':
        raw_text = _call_openai_vision(cfg.get('siliconflow', {}), processed)
    elif provider == 'paddle':
        raw_text = _run_paddle(processed)
    else:
        raise RuntimeError(f'未知的 OCR 供应商: {provider}')

    # 2. 结构化字段提取（兼容多种格式）
    #    a) 先尝试当 JSON/表格 解析（兼容之前那种 prompt 输出）
    fields = _extract_json(raw_text)
    norm = _normalize_fields(fields)
    have_real_value = any(norm.get(k) for k in
                          ('machine_name', 'nic1_name', 'nic1_mac', 'nic2_name',
                           'nic2_mac', 'disk_gb', 'memory_gb', 'sn_number'))

    #    b) 不行就正则在原始文本上兜底（处理「网卡1：xxx\\n网卡名称：yyy」这种纯文本）
    if not have_real_value:
        norm = parse_fields(raw_text)
        norm['_full_text'] = raw_text
    else:
        norm['_full_text'] = '\n'.join(
            f"{k}: {v}" for k, v in norm.items()
            if not k.startswith('_') and v
        )

    norm['_ocr_raw_response'] = raw_text
    return norm


# ---------------- 兼容层（保持接口稳定） ----------------

def parse_fields(full_text) -> dict:
    """兼容层：run_ocr 现在直接返回结构化字段，这里仅做兜底解析

    如果上游调用的是 PaddleOCR 返回的全文，会走正则兜底。
    """
    # 如果已经是结构化字段（dict），直接返回
    if isinstance(full_text, dict):
        return full_text

    text = full_text or ''
    parsed = {}

    # 机器名
    m = re.search(r'机器名[：:]\s*([^\n\r]+)', text)
    if m:
        parsed['machine_name'] = m.group(1).strip()

    # 网卡 1 / 2 —— 忠实保留识别原文（包括 [00000010] 前缀、大小写、单位）
    # 策略：两种匹配方式，取匹配结果更长的
    nic_blocks = []

    # 方式 1：「网卡名称：xxx 物理地址：xxx」（中间可能换行）
    matches = re.findall(
        r'网卡名称\s*[：:]\s*([^\n]+?)\s*\n?\s*物理地址\s*[：:]\s*([0-9A-Fa-f:]{17})',
        text
    )
    if matches:
        nic_blocks.extend(matches)

    # 方式 2：「网卡1：xxx 物理地址：xxx」（中间可能换行）
    if len(nic_blocks) < 2:
        matches = re.findall(
            r'网卡\s*[12][：:]\s*([^\n]+?)\s*\n?\s*物理地址\s*[：:]\s*([0-9A-Fa-f:]{17})',
            text
        )
        for m in matches:
            if not nic_blocks or m[1] not in {b[1] for b in nic_blocks}:
                nic_blocks.append(m)

    if nic_blocks:
        parsed['nic1_name'] = nic_blocks[0][0].strip()
        parsed['nic1_mac'] = nic_blocks[0][1].strip()
    if len(nic_blocks) > 1:
        parsed['nic2_name'] = nic_blocks[1][0].strip()
        parsed['nic2_mac'] = nic_blocks[1][1].strip()

    # 硬盘、内存、SN：识别到啥就保留啥，不去单位/不二次加工
    m = re.search(r'硬盘[：:]\s*([^\n\r]+)', text)
    if m:
        parsed['disk_gb'] = m.group(1).strip()
    m = re.search(r'内存[：:]\s*([^\n\r]+)', text)
    if m:
        parsed['memory_gb'] = m.group(1).strip()
    m = re.search(r'SN\s*号[：:]\s*([^\n\r]+)', text)
    if m:
        parsed['sn_number'] = m.group(1).strip()

    parsed['_extra'] = ''
    return parsed
