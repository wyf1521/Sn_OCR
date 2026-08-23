# Sn_OCR — 计算机信息 OCR 提取系统

> 挂在 Ubuntu 服务器上的内部网页工具。上传类似 `Windows Script Host 计算机信息` 的图片，自动 OCR 识别其中的 **机器名、网卡、MAC、硬盘、内存、SN号** 等信息，结果按用户永久保存到 SQLite，并可一键导出为 Excel。

## 功能

- 🔐 内部账号登录（账号密码，硬编码在 `config.py`）
- 📷 图片上传 → 自动 OCR（默认走大模型 API，可切换本地 PaddleOCR）
- 🧾 结构化字段提取（机器名 / 网卡1 / 网卡2 / MAC / 硬盘 / 内存 / SN）
- 💾 数据按 **用户** 永久保存到 SQLite
- 📊 一键导出当前用户全部记录为 `.xlsx`
- 🌐 简单的 `/api/records` JSON API 便于对接其他系统

## OCR 供应商配置（重点）

OCR 供应商信息全部集中在 **`ocr_config.json`**，随时修改、随时切换：

```json
{
  "provider": "siliconflow",
  "siliconflow": {
    "api_base": "https://api.siliconflow.cn/v1",
    "api_key": "你的API-KEY",
    "model": "deepseek-ai/DeepSeek-OCR",
    "timeout": 120,
    "max_tokens": 4096
  },
  "paddle": { "lang": "ch" }
}
```

- **换供应商**：改 `"provider"` 字段（当前支持 `siliconflow` / `paddle`）。
- **换 API 地址 / 模型 / key**：直接改对应字段即可，无需改代码。
- 大模型 OCR 走 OpenAI 兼容的 `/chat/completions` 接口，因此 SiliconFlow、DeepSeek、通义、智谱、Moonshot 等几乎所有厂商都能用——只需改 `api_base` + `api_key` + `model` 三个值。
- 若切换到 `paddle`（本地离线 OCR），需要 `pip install paddleocr`（见 `requirements.txt` 注释）。

## 目录结构

```
Sn_OCR/
├── app.py              # Flask 主入口
├── config.py           # 配置（用户、路径）
├── ocr_config.json     # OCR 供应商配置（key/模型/切换）
├── database.py         # SQLite 数据访问层
├── ocr_engine.py       # OCR 引擎（多供应商）+ 字段解析
├── excel_export.py     # openpyxl 导出
├── templates/          # Jinja2 模板
│   ├── login.html
│   └── index.html
├── uploads/            # 上传的原始图片
├── data/
│   ├── ocr_data.db     # SQLite 数据库
│   └── exports/<user>/ocr_records.xlsx
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

> **使用第三方大模型 OCR（默认）** 无需安装任何模型，直接填 `ocr_config.json` 的 key 即可。  
> **若要用本地 PaddleOCR 兜底**，再执行：`pip install paddleocr`（3.x 自带 paddlex，无需单独装 paddlepaddle）。

### 3. 配置 OCR 供应商

编辑 `ocr_config.json`，填写你的 API key：
```json
"api_key": "sk-xxxxxxxxxxxxxxxx"
```

### 4. 修改账号

编辑 `config.py` 里的 `USERS` 字典，把 `admin` / `staff` 的密码改成强密码：
```python
USERS = {
    'zhangsan': 'YourStrongPwd!',
    'lisi':     'AnotherPwd!',
}
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

    client_max_body_size 20M;

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

## 内置默认账号

| 账号 | 密码 |
|------|------|
| admin | admin@2026 |
| staff | staff@2026 |

> ⚠️ 部署后务必修改 `config.py` 中的密码！

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/records` | 当前用户全部记录 JSON |

## 许可

仅供内部使用。
