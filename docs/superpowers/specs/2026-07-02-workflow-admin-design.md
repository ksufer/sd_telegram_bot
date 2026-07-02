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
| 根 | `key` | str | 是 | 唯一标识，`[a-z0-9_-]+`，与文件名一致 |
| 根 | `enabled` | bool | 是 | `false` 时 Bot 跳过该工作流 |
| menu | `emoji` | str | 否 | 菜单按钮图标 |
| menu | `label` | str | 是 | 短名称 |
| menu | `description` | str | 是 | 一行描述 |
| menu | `how_to` | str | 是 | 多行使用说明 |
| menu | `input_type` | str | 是 | `"text"` 或 `"photo"` |
| menu | `backend` | str | 是 | 固定 `"comfyui"`（第一版） |
| comfy | `label` | str | 否 | 设置页显示的工作流名称 |
| comfy | `workflow_file` | str | 是 | ComfyUI workflow JSON 文件名（相对于 `data/comfy_workflows/`） |
| comfy | `is_img2img` | bool | 是 | 是否图生图模式 |
| comfy | `prompt_node` | str/list | 是 | 提示词节点 ID |
| comfy | `prompt_key` | str | 是 | 提示词 inputs key |
| comfy | `seed_node` | str/list | 是 | 种子节点 ID |
| comfy | `seed_key` | str | 是 | 种子 inputs key |
| comfy | `model_node` | str/list | 否 | 模型节点 ID |
| comfy | `model_key` | str | 否 | 模型 inputs key |
| comfy | `model_loader_class` | str | 否 | 节点 class_type（用于模型列表查询） |
| comfy | `model_selectable` | bool | 否 | 是否允许用户切换模型（默认 true） |
| comfy | `width_node` | str | 否 | 宽度节点 ID |
| comfy | `height_node` | str | 否 | 高度节点 ID |
| comfy | 其他节点字段 | str/list/dict | 否 | 如 `load_image_node`、`lora_node`、各类 switch 节点等 |
| comfy | `default_model` | str | 否 | 默认模型名 |
| 根 | `user_configurable` | []str | 是 | 用户可编辑的 settings key 列表 |

### 3.3 Bot 加载逻辑

`config.py` 中新增动态加载函数：

```python
def _load_workflows():
    """从 data/workflows/ 加载所有配置。不存在时回退默认值。"""
    import json
    from pathlib import Path

    wf_dir = Path("data/workflows")
    if not wf_dir.exists():
        return _DEFAULT_WORKFLOW_REGISTRY, _DEFAULT_COMFY_WORKFLOWS

    registry = []
    comfy_workflows = {}
    for f in sorted(wf_dir.glob("*.json")):
        try:
            with open(f) as fp:
                data = json.load(fp)
        except Exception:
            logger.warning("跳过无效配置: %s", f.name)
            continue
        if not data.get("enabled", True):
            continue
        registry.append({
            "key": data["key"],
            **data.get("menu", {}),
        })
        if data.get("comfy"):
            cfg = data["comfy"]
            cfg["user_configurable"] = data.get("user_configurable", [])
            comfy_workflows[data["key"]] = cfg
    return registry, comfy_workflows
```

默认值保留在 `config.py` 中，当 `data/workflows/` 目录不存在或为空时作为回退。

### 3.4 菜单双重判断规则

`_comfy_settings_menu()` 中每个设置项的显示条件改为同时检查 `user_configurable` 和节点能力：

```python
uc = wf_config.get("user_configurable", [])

# 尺寸
if {"comfy_width", "comfy_height"}.issubset(uc) and wf_config.get("width_node"):
    show_size_button()

# 视频参数
if "output_type" == "video" and {"comfy_video_aspect", "comfy_video_resolution"}.issubset(uc):
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
```

**规则：一个设置项只有在管理员勾选且工作流实际支持时，才在用户菜单中显示。**

## 4. Web 管理页

### 4.1 文件结构

```
admin/
├── app.py                  # Flask 路由 + 登录中间件
├── workflow_store.py       # 读写 workflow 配置，原子写入
├── validators.py           # 校验节点 ID / inputs key
├── templates/
│   ├── base.html           # 公共布局
│   ├── login.html          # 登录页
│   ├── list.html           # 工作流列表
│   ├── detail.html         # 详情页（含校验报告）
│   └── form.html           # 新建/编辑表单
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
| `/detail/<key>` | GET | 查看详情 + 校验报告 |
| `/delete/<key>` | POST | 禁用工作流（移至 `.trash/`） |
| `/api/validate-workflow` | POST | 上传 ComfyUI workflow JSON 并返回节点校验结果 |

### 4.3 校验规则

**基础校验（表单级）：**
- key 格式 `^[a-z0-9_-]+$`，不能与已有 key 重复
- menu.label、menu.description、menu.how_to 非空
- menu.input_type 必须是 `text` 或 `photo`
- comfy.workflow_file 非空，且对应文件在 `data/comfy_workflows/` 下存在

**结构校验（workflow JSON 文件级）：**
- 文件是合法 JSON
- 顶层是 dict（`{ "node_id": { "class_type": "...", "inputs": {...} } }`）

**节点校验（填写值与实际 workflow 匹配）：**
- 每个已填写的 `*_node` 值必须在 workflow JSON 的顶层 key 中存在
- 每个对应的 `*_key` 值必须在对应节点的 `inputs` 中存在
- `model_loader_class` 必须与对应节点的 `class_type` 一致

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
    """移至 .trash/ 而非直接删除。"""
    src = Path("data/workflows") / f"{key}.json"
    trash = Path("data/workflows/.trash")
    trash.mkdir(exist_ok=True)
    src.rename(trash / f"{key}.json")
```

### 4.5 认证

- `ADMIN_PASSWORD` 和 `FLASK_SECRET_KEY` 从 `.env` 读取
- 登录后使用 Flask session 保持状态
- 所有页面路由通过 `@login_required` 装饰器保护

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
RUN uv sync --frozen --extra admin
COPY . .
CMD ["uv", "run", "python", "-m", "admin.app"]
```

### 5.3 pyproject.toml 新增依赖

```toml
[project.optional-dependencies]
admin = ["flask"]
```

## 6. 迁移脚本

提供一个一次性脚本，将现有硬编码配置导出为 JSON 文件：

```bash
uv run python -m scripts.export_workflows
```

输出目录：`data/workflows/`，每个现有工作流一个 JSON 文件。同时将对应的 ComfyUI workflow JSON 从 `data/` 移到 `data/comfy_workflows/`。

## 7. 实施阶段

| 阶段 | 内容 | 验收标准 |
|------|------|---------|
| **1** | 配置文件化 | Bot 从 `data/workflows/*.json` 加载，正常生成，不存在的目录回退默认值 |
| **2** | 只读管理页 | 列表 + 详情页 + 节点校验报告，无需重启 Bot |
| **3** | 编辑管理页 | 新建/编辑/禁用/表单校验/原子写入，改完重启 Bot 生效 |
| **4** | 菜单双重判断 | `user_configurable` + 节点能力共同控制菜单显示 |
| **5** | 热加载（可选） | `services/workflow_config.py` + `/reload` 命令 |
