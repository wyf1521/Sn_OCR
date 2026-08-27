# Sn_OCR — 计算机信息 OCR 提取系统

> 挂在 Ubuntu 服务器上的内部网页工具。上传类似 `Windows Script Host 计算机信息` 的图片，自动 OCR 识别其中的 **机器名、网卡、MAC、硬盘、内存、SN号** 等信息，结果永久保存到 SQLite，并可一键导出为 Excel。

## 功能

- 🔐 内部账号登录（管理员密码从 `SN_OCR_ADMIN_PASSWORD` 环境变量读取）
- 📷 图片上传 → 自动 OCR（调用 OpenAI 兼容的大模型 API）
- 🧾 结构化字段提取（机器名 / 网卡1 / 网卡2 / MAC / 硬盘 / 内存 / SN）
- 💾 数据永久保存到 SQLite，所有登录用户共享记录
- 📊 一键导出已审核记录为 `.xlsx`，并按机器名排序、嵌入原图
- 🌐 简单的 `/api/records` JSON API 便于对接其他系统

## OCR 配置（重点）

OCR 供应商信息全部集中在本地配置文件 **`ocr_config.json`**。该文件包含 API Key，不上传到 Git；仓库提供不含密钥的 **`ocr_config.example.json`**：

```json
{
  "provider": "siliconflow",
  "siliconflow": {
    "api_base": "https://api.siliconflow.cn/v1",
    "api_key": "你的API-KEY",
    "model": "deepseek-ai/DeepSeek-OCR",
    "timeout": 120,
    "max_tokens": 4096
  }
}
```

- **当前供应商**：`siliconflow`，通过 OpenAI 兼容接口调用视觉 OCR 模型。
- **换 API 地址 / 模型 / key**：直接改对应字段即可，无需改代码。
- 大模型 OCR 走 OpenAI 兼容的 `/chat/completions` 接口，因此 SiliconFlow、DeepSeek、通义、智谱、Moonshot 等几乎所有厂商都能用——只需改 `api_base` + `api_key` + `model` 三个值。

## 目录结构

```
Sn_OCR/
├── app.py              # Flask 主入口
├── config.py           # 配置（用户、路径）
├── ocr_config.example.json # OCR 配置模板（不含 key）
├── database.py         # SQLite 数据访问层
├── ocr_engine.py       # OCR 引擎（多供应商）+ 字段解析
├── excel_export.py     # openpyxl 导出
├── templates/          # Jinja2 模板
│   ├── login.html
│   └── index.html
├── uploads/            # 上传的原始图片（运行时生成）
├── data/
│   ├── ocr_data.db     # SQLite 数据库
│   └── exports/ocr_records.xlsx
├── requirements.txt
└── README.md
```

## Ubuntu 服务器部署

### 1. 安装系统依赖

```bash
sudo apt update
sudo apt install -y python3.10 python3.10-venv python3-pip libgomp1
```

### 2. 创建虚拟环境并安装依赖

```bash
cd /opt/Sn_OCR   # 把代码放这里
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

> 使用第三方大模型 OCR 无需安装本地模型。部署时先执行 `cp ocr_config.example.json ocr_config.json`，再填写 `ocr_config.json` 的 API key；`ocr_config.json` 只保存在服务器本地。

### 3. 配置 OCR

复制配置模板并填写 API key：
```bash
cp ocr_config.example.json ocr_config.json
```

编辑本地的 `ocr_config.json`：
```json
"api_key": "sk-xxxxxxxxxxxxxxxx"
```

### 4. 修改账号

账号密码不写入代码，部署前设置环境变量：
```bash
export SECRET_KEY="$(openssl rand -hex 32)"
export SN_OCR_ADMIN_PASSWORD='YourStrongPwd!'
```

### 5. 启动

开发模式快速验证：
```bash
python app.py
# 浏览器打开 http://服务器IP:5000
```

生产环境推荐 **gunicorn** + nginx：

```bash
pip install gunicorn
gunicorn -w 2 -b 0.0.0.0:5000 app:app
```

nginx 反代示例（`/etc/nginx/sites-available/sn_ocr`）：
```
server {
    listen 80;
    server_name ocr.your-domain.com;

    client_max_body_size 512M;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### 6. （可选）用 systemd 守护进程

`/etc/systemd/system/sn_ocr.service`：
```ini
[Unit]
Description=Sn OCR service
After=network.target

[Service]
WorkingDirectory=/opt/Sn_OCR
ExecStart=/opt/Sn_OCR/venv/bin/gunicorn -w 2 -b 127.0.0.1:5000 app:app
Restart=always
User=www-data

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now sn_ocr
```

## 数据备份

`data/ocr_data.db` 是唯一的持久化文件：
```bash
# 每天凌晨备份
0 2 * * * cp /opt/Sn_OCR/data/ocr_data.db /backup/ocr_$(date +\%F).db
```
图片文件放在 `uploads/`，可同步备份到 OSS / NAS。

## 字段解析规则

针对 `Windows Script Host` 弹窗做了正则匹配，识别以下字段：

| 字段 | 示例 | 正则关键 |
|------|------|---------|
| 机器名 | `YCSPBG000095` | `机器名：xxx` |
| 网卡1 名称 | `[00000010] Intel(R) Ethernet Connection (24) i219-V` | `网卡1：` + `网卡名称：` |
| 网卡1 MAC | `80:CA:52:C8:1A:21` | `物理地址：` |
| 网卡2 名称 | `[00000011] Realtek ... WiFi 6 ...` | 同上 |
| 网卡2 MAC | `14:B5:CD:AC:F7:3B` | 同上 |
| 硬盘 | `467.365` GB | `硬盘：xxx GB` |
| 内存 | `15.49` GB | `内存：xxx GB` |
| SN号 | `PW0PMRZD` | `SN号：xxx` |

> 如果图片中增加了新的字段或格式变动，编辑 `ocr_engine.py` 里的 `parse_fields()` 即可，对应数据库会自动适应。

## 账号

账号固定为 `admin`，密码由 `SN_OCR_ADMIN_PASSWORD` 环境变量提供，不再写入仓库。

未设置该环境变量时不会启用管理员登录，避免部署时使用空密码。生产环境还应设置稳定的 `SECRET_KEY`；开发环境未设置时会使用临时密钥，重启后已有会话需要重新登录。

## API

### 外部 OCR 接口

外部程序使用 Bearer Token 调用，不需要网页登录 Session。先在服务端配置 Token 并重启应用：

```bash
export SN_OCR_API_TOKEN='请替换为长度足够的随机字符串'
```

推荐使用密码生成器生成 Token，例如：

```bash
openssl rand -hex 32
```

接口地址：`POST /api/v1/ocr`（`POST /api/v1/upload` 是兼容别名）。请求必须是 `multipart/form-data`，图片字段名为 `file`；可以重复传多个 `file` 字段进行批量识别。支持格式：`png`、`jpg`、`jpeg`、`bmp`、`webp`。单张图片最大 20MB，单次最多 500 张，整个请求最大 512MB。

请求头：

```http
Authorization: Bearer <SN_OCR_API_TOKEN>
```

可选表单字段 `duplicate_policy`：

- `skip`（默认）：SN 已存在时跳过，不覆盖原记录。
- `overwrite`：SN 已存在时覆盖原记录，并将审核状态重置为未审核。

单张图片示例：

```bash
curl -X POST 'http://127.0.0.1:5000/api/v1/ocr' \
  -H 'Authorization: Bearer YOUR_API_TOKEN' \
  -F 'file=@./computer.png' \
  -F 'duplicate_policy=skip'
```

批量图片示例：

```bash
curl -X POST 'http://127.0.0.1:5000/api/v1/ocr' \
  -H 'Authorization: Bearer YOUR_API_TOKEN' \
  -F 'file=@./computer-01.png' \
  -F 'file=@./computer-02.jpg' \
  -F 'duplicate_policy=overwrite'
```

Python 示例：

```python
import requests

url = 'http://127.0.0.1:5000/api/v1/ocr'
headers = {'Authorization': 'Bearer YOUR_API_TOKEN'}
with open('computer.png', 'rb') as image:
    response = requests.post(
        url,
        headers=headers,
        files={'file': ('computer.png', image, 'image/png')},
        data={'duplicate_policy': 'skip'},
        timeout=180,
    )
response.raise_for_status()
print(response.json())
```

JavaScript（Node.js 18+）示例：

```javascript
import fs from 'node:fs';
import { FormData, File } from 'node:buffer';

const body = new FormData();
body.append('file', new File([fs.readFileSync('./computer.png')], 'computer.png', {
  type: 'image/png',
}));
body.append('duplicate_policy', 'skip');

const response = await fetch('http://127.0.0.1:5000/api/v1/ocr', {
  method: 'POST',
  headers: { Authorization: 'Bearer YOUR_API_TOKEN' },
  body,
});
const result = await response.json();
if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
console.log(result.fields);
```

不要手动设置 `Content-Type` 请求头，`FormData` 会自动生成带 boundary 的正确值。

单张图片成功响应示例：

```json
{
  "success": true,
  "record_id": 123,
  "provider": "siliconflow",
  "model": "deepseek-ai/DeepSeek-OCR",
  "elapsed_seconds": 4.21,
  "field_count": 8,
  "fields": {
    "machine_name": "YCSPGW000001",
    "nic1_name": "Realtek PCIe GbE Family Controller",
    "nic1_mac": "00:11:22:33:44:55",
    "sn_number": "ABC123"
  },
  "image_name": "computer.png",
  "upload_time": "2026-08-27 15:30:00"
}
```

批量请求返回 `total`、`succeeded`、`failed` 和 `results`；每个 `results` 元素使用与单张响应相同的字段。重复跳过时，元素仍然是 `success: true`，并带有 `duplicate: true`；处理失败时为 `success: false` 并带 `error`。

常见 HTTP 状态码：

| 状态码 | 含义 |
|------|------|
| 200 | 单张或批量请求中至少有一张处理成功 |
| 400 | 缺少图片、字段格式错误或请求体不是 multipart |
| 401 | Token 缺失或无效 |
| 413 | 超过单次请求大小或图片数量限制 |
| 500 | 单张请求处理失败，或批量请求全部失败 |
| 503 | 服务端没有配置 `SN_OCR_API_TOKEN` |

浏览器页面使用的接口仍然是 `/upload`；已登录用户可以通过 `GET /api/records` 获取历史记录，通过页面完成审核和导出。

## 许可

仅供内部使用。
