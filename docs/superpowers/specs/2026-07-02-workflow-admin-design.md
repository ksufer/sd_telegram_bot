# 工作流配置管理 Web 面板 — 设计规格书

## 1. 概述

将当前硬编码在 `config.py` 中的 `WORKFLOW_REGISTRY` 和 `COMFY_WORKFLOWS` 迁移到 `data/workflows/*.json` 文件，并提供一个独立的 Flask Web 管理面板用于 CRUD 操作。

## 2. 架构

```
┌──────────────────────────────────────────────┐
│  Docker Compose                              │
│                                              │
│  sd-bot (bot.py)            端口: 无         │
│    读取: data/workflows/*.json               │
│                                              │
│  sd-admin (Flask web admin) 端口: 127.0.0.1:8080 │
│    读写: data/workflows/*.json               │
│                                              │
│  共享卷:                                      │
│    ./data/workflows/:/app/data/workflows/    │
│    ./data/comfy_workflows/:/app/data/comfy_workflows/ │
└──────────────────────────────────────────────┘
```

- Bot 和 Admin 共享 `data/workflows/` 和 `data/comfy_workflows/` 目录
- Admin 端口绑定 `127.0.0.1`（不直接暴露公网），通过反向代理或内网访问
- 第一版配置变更后需**重启 Bot 生效**（热加载由阶段 5 实现）

## 3. 数据格式

### 3.1 JSON schema (schema_version: 1)

每个工作流一个文件：`data/workflows/{key}.json`

```json
{
  "schema_version": 1,
  "key": "zit-pussy",
  "enabled": true,
  "menu": {
    "emoji": "💦",
    "label": "ZIT Pussy",
    "description": "文生图 + Pussy 精修 + 2x 放大",
    "how_to": "直接发送提示词即可，Bot 会自动生成并修复脸部细节。",
    "input_type": "text",
    "backend": "comfyui"
  },
  "comfy": {
    "label": "ZIT Pussy（文生图+精修+放大+脸部修复）",
    "workflow_file": "zit-up-pussy-face.json",
    "is_img2img": false,
    "model_selectable": true,
    "prompt_node": "96",
    "prompt_key": "text",
    "seed_node": "97",
    "seed_key": "seed",
    "model_node": "95",
    "model_key": "unet_name",
    "model_loader_class": "UNETLoader",
    "width_node": "91",
    "width_key": "width",
    "height_node": "91",
    "height_key": "height",
    "default_model": "moodyProMix_zitV13.safetensors",
    "upscale_switch_node": "101",
    "upscale_switch_key": "image",
    "upscale_switch_on": ["88", 0],
    "upscale_switch_off": ["93", 0],
    "pussydetailer_switch_node": "111",
    "pussydetailer_switch_key": "image",
    "sd_upscale_node": "88",
    "sd_upscale_seed_key": "seed",
    "lora_node": "102",
    "detailer_prompt_node": "103",
    "detailer_prompt_key": "text",
    "facedetailer_switch_node": "108",
    "facedetailer_switch_key": "images",
    "face_detailer_prompt_node": "115",
    "face_detailer_prompt_key": "text",
    "sd_upscale_prompt_node": "120",
    "sd_upscale_prompt_key": "text"
  },
  "user_configurable": [
    "comfy_model",
    "comfy_seed",
    "comfy_width",
    "comfy_height",
    "comfy_translate",
    "comfy_upscale_enabled",
    "comfy_pussydetailer_enabled",
    "comfy_facedetailer_enabled",
    "comfy_lora_variant",
    "comfy_face_prompt",
    "comfy_prompt"
  ]
}
```

### 3.2 字段说明

| 层级 | 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|------|
| 根 | `schema_version` | int | 是 | 固定为 1，用于未来格式迁移 |
| 根 | `key` | str | 是 | 唯一标识，`[a-z0-9_-]+$`，与文件名一致 |
| 根 | `enabled` | bool | 是 | `false` 时 Bot 跳过该工作流 |
| menu | `emoji` | str | 否 | 菜单按钮图标 |
| menu | `label` | str | 是 | 短名称 |
| menu | `description` | str | 是 | 一行描述 |
| menu | `how_to` | str | 是 | 多行使用说明 |
| menu | `input_type` | str | 是 | `"text"` 或 `"photo"` |
| menu | `backend` | str | 是 | 固定 `"comfyui"`（第一版） |
| comfy | `label` | str | 否 | 设置页显示的工作流名称 |
| comfy | `workflow_file` | str | 是 | ComfyUI workflow JSON **文件名**（不允许含 `/`、`\`、`..`） |
| comfy | `is_img2img` | bool | 是 | 是否图生图模式 |
| comfy | `prompt_node` | str/list | 是 | 提示词节点 ID。若为 list，`prompt_key` 应用于所有节点 |
| comfy | `prompt_key` | str | 是 | 提示词 inputs key |
| comfy | `seed_node` | str/list | 是 | 种子节点 ID。若为 list，`seed_key` 应用于所有节点 |
| comfy | `seed_key` | str | 是 | 种子 inputs key |
| comfy | `model_node` | str/list | 否 | 模型节点 ID |
| comfy | `model_key` | str | 否 | 模型 inputs key |
| comfy | `model_loader_class` | str | 否 | 节点 class_type（用于模型列表查询） |
| comfy | `model_selectable` | bool | 否 | 是否允许用户切换模型（默认 true）。`true` 时 `model_node`/`model_key`/`model_loader_class` 必填 |
| comfy | `width_node` | str | 否 | 宽度节点 ID |
| comfy | `width_key` | str | 否 | 宽度 inputs key |
| comfy | `height_node` | str | 否 | 高度节点 ID |
| comfy | `height_key` | str | 否 | 高度 inputs key |
| comfy | 其他节点字段 | str/list/dict | 否 | 如 `load_image_node`、`lora_node`、各类 switch 节点等 |
| comfy | `default_model` | str | 否 | 默认模型名 |
| 根 | `user_configurable` | []str | 是 | 用户可编辑的 settings key 列表 |

### 3.3 Bot 加载逻辑

`config.py` 中新增动态加载函数。**关键语义：**

- 目录不存在或没有任何 `.json` 文件 → 回退硬编码默认配置
- 目录存在、有文件、但全部被禁用 → **返回空列表 + 打印 warning**（不回退默认）
- 配置文件 `key` 与文件名不一致 → 跳过
- `schema_version` 不为 1 → 跳过

```python
def _load_workflows():
    """从 data/workflows/ 加载所有配置。"""
    import json
    from pathlib import Path

    wf_dir = Path("data/workflows")
    if not wf_dir.exists():
        return _DEFAULT_WORKFLOW_REGISTRY, _DEFAULT_COMFY_WORKFLOWS

    files = sorted(wf_dir.glob("*.json"))
    if not files:
        return _DEFAULT_WORKFLOW_REGISTRY, _DEFAULT_COMFY_WORKFLOWS

    registry = []
    comfy_workflows = {}

    for f in files:
        try:
            with open(f, encoding="utf-8") as fp:
                data = json.load(fp)

            if data.get("schema_version") != 1:
                logger.warning("跳过不支持的配置版本: %s", f.name)
                continue

            key = data["key"]
            if f.stem != key:
                logger.warning("跳过 key 与文件名不一致的配置: %s", f.name)
                continue

            if not data.get("enabled", True):
                continue

            menu = data.get("menu", {})
            registry.append({
                "key": key,
                **menu,
            })

            if data.get("comfy"):
                # 复制而非原地修改，避免影响原始 dict
                comfy = {
                    **data["comfy"],
                    "user_configurable": data.get("user_configurable", []),
                }
                comfy_workflows[key] = comfy

        except Exception:
            logger.warning("跳过无效配置: %s", f.name, exc_info=True)
            continue

    return registry, comfy_workflows
```

默认值保留在 `config.py` 中，当 `data/workflows/` 目录不存在或为空时作为回退。

### 3.4 菜单双重判断规则

`_comfy_settings_menu()` 中每个设置项的显示条件改为同时检查 `user_configurable` 和节点能力：

```python
uc = wf_config.get("user_configurable", [])

# 尺寸：同时检查 node 和 key
if (
    {"comfy_width", "comfy_height"}.issubset(uc)
    and wf_config.get("width_node") and wf_config.get("width_key")
    and wf_config.get("height_node") and wf_config.get("height_key")
):
    show_size_button()

# 视频参数
if (
    wf_config.get("output_type") == "video"
    and {"comfy_video_aspect", "comfy_video_resolution", "comfy_video_frames"}.issubset(uc)
):
    show_video_buttons()

# 三级开关
if "comfy_upscale_enabled" in uc and wf_config.get("upscale_switch_node"):
    show_upscale_toggle()
if "comfy_pussydetailer_enabled" in uc and wf_config.get("pussydetailer_switch_node"):
    show_pussydetailer_toggle()
if "comfy_facedetailer_enabled" in uc and wf_config.get("facedetailer_switch_node"):
    show_facedetailer_toggle()

# LoRA 变体
if "comfy_lora_variant" in uc and wf_config.get("lora_node"):
    show_lora_variant()

# 模型切换：model_selectable=true + 必须字段齐全
if (
    "comfy_model" in uc
    and wf_config.get("model_selectable", True)
    and wf_config.get("model_node")
    and wf_config.get("model_key")
):
    show_model_button()
```

**规则：一个设置项只有在管理员勾选且工作流实际支持时，才在用户菜单中显示。**

### 3.5 `*_node` 为 list 时的校验规则

若 `*_node` 字段为 list，则对应的 `*_key` 必须为 str，并应用于 list 中所有节点：

```python
nodes = value if isinstance(value, list) else [value]
for node_id in nodes:
    check node_id exists in workflow JSON
    check key exists in node.inputs
```

第一版不支持每个节点有不同的 key。未来如需支持再扩展 schema。

## 4. Web 管理页

### 4.1 文件结构

```
admin/
├── __init__.py              # 空文件，使 admin 可作为包导入
├── app.py                   # Flask 路由 + 登录中间件
├── workflow_store.py        # 读写 workflow 配置，原子写入
├── validators.py            # 校验节点 ID / inputs key / class_type
├── templates/
│   ├── base.html            # 公共布局
│   ├── login.html           # 登录页
│   ├── list.html            # 工作流列表
│   ├── detail.html          # 详情页（含校验报告 + JSON 预览）
│   └── form.html            # 新建/编辑表单
└── static/
    └── style.css
```

### 4.2 路由

| 路由 | 方法 | 说明 |
|------|------|------|
| `/login` | GET/POST | 登录页 |
| `/logout` | GET | 退出登录 |
| `/` | GET | 工作流列表 |
| `/new` | GET/POST | 新建工作流 |
| `/edit/<key>` | GET/POST | 编辑工作流 |
| `/detail/<key>` | GET | 查看详情 + 校验报告 + JSON 预览 |
| `/disable/<key>` | POST | 设置 `"enabled": false`（软禁用，文件留在原处） |
| `/enable/<key>` | POST | 设置 `"enabled": true` |
| `/archive/<key>` | POST | 归档至 `.trash/`（确认后移动文件） |
| `/api/upload-comfy-workflow` | POST | 上传原始 ComfyUI workflow JSON 到 `data/comfy_workflows/` |
| `/api/validate-mapping` | POST | 根据已上传的 workflow_file 和表单字段校验节点映射 |

### 4.3 校验规则

**基础校验（表单级）：**
- key 格式 `^[a-z0-9_-]+$`，不能与已有 key 重复
- menu.label、menu.description、menu.how_to 非空
- menu.input_type 必须是 `text` 或 `photo`
- comfy.workflow_file 非空，且只能为纯文件名（不允许 `/`、`\`、`..`）
- comfy.workflow_file 对应文件在 `data/comfy_workflows/` 下必须存在
- `model_selectable=true` 时，`model_node`、`model_key`、`model_loader_class` 三者必填

**workflow_file 路径安全校验：**

```python
from pathlib import Path

def validate_workflow_file(name: str) -> None:
    p = Path(name)
    if p.name != name or ".." in p.parts:
        raise ValueError("workflow_file 只能填写文件名，不允许路径")
```

**结构校验（workflow JSON 文件级）：**
- 文件是合法 JSON
- 顶层是 dict（`{ "node_id": { "class_type": "...", "inputs": {...} } }`）

**节点校验（填写值与实际 workflow 匹配）：**

| 校验项 | 规则 |
|--------|------|
| `*_node` 存在 | 所有已填写的 `*_node` 值（展开 list）必须在 workflow JSON 的顶层 key 中存在 |
| `*_key` 存在 | 每个 `*_key` 必须在对应节点的 `inputs` 中存在 |
| `model_loader_class` 匹配 | 必须与 model 节点的 `class_type` 一致 |
| `load_image_nodes` 特殊校验 | 如果为 dict（`{"role": {"node": "...", "key": "..."}}`），逐个检验 node 和 key |

### 4.4 原子写入

```python
# workflow_store.py
import json
from pathlib import Path

def save_workflow(data: dict) -> None:
    key = data["key"]
    path = Path("data/workflows") / f"{key}.json"
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)

def disable_workflow(key: str) -> None:
    """设置 enabled=false，文件留在原处。"""
    path = Path("data/workflows") / f"{key}.json"
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data["enabled"] = False
    save_workflow(data)

def archive_workflow(key: str) -> None:
    """移至 .trash/ 目录。"""
    src = Path("data/workflows") / f"{key}.json"
    trash = Path("data/workflows/.trash")
    trash.mkdir(exist_ok=True)
    src.rename(trash / f"{key}.json")
```

### 4.5 认证

- `ADMIN_PASSWORD` 和 `FLASK_SECRET_KEY` 从 `.env` 读取
- 启动时检查二者非空，为空则拒绝启动
- 登录后使用 Flask session 保持状态
- 所有页面路由通过 `@login_required` 装饰器保护
- Session cookie 安全配置：

```python
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)
```

## 5. Docker 配置

### 5.1 docker-compose.yml 新增服务

```yaml
sd-admin:
  build:
    context: .
    dockerfile: Dockerfile.admin
  container_name: sd-admin
  restart: unless-stopped
  ports:
    - "127.0.0.1:8080:8080"
  env_file:
    - .env
  volumes:
    - ./data:/app/data
```

### 5.2 Dockerfile.admin

```dockerfile
FROM python:3.12-slim

WORKDIR /app
RUN pip install uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --extra admin --no-dev

COPY . .

ENV PYTHONPATH=/app

CMD ["uv", "run", "python", "-m", "admin.app"]
```

### 5.3 pyproject.toml 新增依赖

```toml
[project.optional-dependencies]
admin = ["flask"]
```

### 5.4 需要确认

- `admin/` 下需创建 `__init__.py`（空文件）
- `pyproject.toml` 需包含基本打包配置（`[project] name` 等）使 `uv sync` 能识别本项目

## 6. 迁移脚本

提供一个一次性脚本，将现有硬编码配置导出为 JSON 文件：

```bash
uv run python -m scripts.export_workflows --dry-run
uv run python -m scripts.export_workflows --write
```

### 行为

| 模式 | 行为 |
|------|------|
| `--dry-run` | 打印每个工作流将生成的 JSON 内容，不做任何写入 |
| `--write` | 将配置导出为 `data/workflows/{key}.json`，**复制** ComfyUI workflow JSON 到 `data/comfy_workflows/`（不移动原文件） |

**原则：迁移脚本只新增文件，不删除或移动任何旧文件。** 旧文件清理在验证通过后单独操作。

## 7. 实施阶段

| 阶段 | 内容 | 验收标准 |
|------|------|---------|
| **1** | 配置文件化 | Bot 从 `data/workflows/*.json` 加载；目录不存在/为空时回退默认值；全部禁用时返回空列表 |
| **2** | 只读管理页 | 列表 + 详情页 + 节点校验报告 + JSON 预览；启动时检查 `ADMIN_PASSWORD`/`FLASK_SECRET_KEY` |
| **3** | 编辑管理页 | 新建/编辑/软禁用/归档 + 表单校验 + 原子写入；改完重启 Bot 生效 |
| **4** | 菜单双重判断 | `user_configurable` + 节点能力共同控制菜单显示 |
| **5** | 热加载（可选） | `services/workflow_config.py` + `/reload` 命令 |
