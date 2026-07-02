# 工作流配置管理 Web 面板 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将工作流配置从硬编码 `config.py` 迁移到 `data/workflows/*.json` 文件，并提供独立 Flask Web 管理面板。

**Architecture:** `config.py` 启动时动态加载 `data/workflows/*.json`；管理员通过独立 Docker 容器中的 Flask 面板 CRUD 配置文件；Bot 重启后生效。

**Tech Stack:** Python 3.12, Flask, Jinja2, uv, Docker Compose

## Global Constraints

- Python 3.12, uv 管理依赖, lockfile 为 `uv.lock`
- Bot 入口为 `bot.py`, `uv run python bot.py` 启动
- Docker 多平台 Compose (Linux: `network_mode: host`, Windows: `extra_hosts`)
- 没有测试框架配置, 不试图运行 pytest
- 配置变更第一版需要重启 Bot 生效 (阶段 5 前不做热加载)
- `workflow_file` 只能是纯文件名, 不允许路径
- 原子写入: 先写 `.tmp` 再 `os.replace`
- **路径常量**: admin 侧集中 `data_dir/workflows` / `data_dir/comfy_workflows` 路径，不散落字面量
- **video frames key**: 执行前 grep 确认现有 settings key 为 `comfy_video_frames`（非 `comfy_video_length`），`user_configurable` 只使用现有 key

---

### Task 1: 创建迁移脚本 `scripts/export_workflows.py`

**Files:**
- Create: `scripts/__init__.py`
- Create: `scripts/export_workflows.py`

**Interfaces:**
- Produces: none (standalone one-shot script)

- [ ] **Step 1: 创建 `scripts/__init__.py`**

```bash
mkdir -p scripts && touch scripts/__init__.py
```

- [ ] **Step 2: 创建 `scripts/export_workflows.py`**

```python
"""将 config.py 中硬编码的工作流配置导出为 data/workflows/*.json 文件。"""

import json
import os
import shutil
import sys
from pathlib import Path

from config import WORKFLOW_REGISTRY, COMFY_WORKFLOWS

WORKFLOW_DIR = Path("data/workflows")
COMFY_DIR = Path("data/comfy_workflows")
DATA_DIR = Path("data")


def export(dry_run: bool = False) -> None:
    """导出所有工作流配置。"""
    if not dry_run:
        WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)
        COMFY_DIR.mkdir(parents=True, exist_ok=True)

    for entry in WORKFLOW_REGISTRY:
        key = entry["key"]
        comfy_key = entry["comfy_workflow"]
        comfy_cfg = COMFY_WORKFLOWS.get(comfy_key, {})

        # 复制 ComfyUI workflow JSON 到新目录
        workflow_path = comfy_cfg.get("path", "")
        src = Path(workflow_path)
        dst = COMFY_DIR / src.name
        if src.exists() and not dry_run:
            shutil.copy2(src, dst)

        data = {
            "schema_version": 1,
            "key": key,
            "enabled": True,
            "menu": {
                "emoji": entry.get("emoji", ""),
                "label": entry["label"],
                "description": entry["description"],
                "how_to": entry.get("how_to", ""),
                "input_type": entry.get("input_type", "text"),
                "backend": entry.get("backend", "comfyui"),
            },
            "comfy": {
                **comfy_cfg,
                "workflow_file": src.name,
            },
            "user_configurable": _default_user_configurable(comfy_cfg),
        }
        # 移除旧 path 字段（已替换为 workflow_file）
        data["comfy"].pop("path", None)

        if dry_run:
            print(f"\n=== {key}.json ===")
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            out_path = WORKFLOW_DIR / f"{key}.json"
            tmp = out_path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            tmp.replace(out_path)
            print(f"Wrote {out_path}")

    print(f"\nDry-run: {dry_run}. Files count: {len(WORKFLOW_REGISTRY)}")


def _default_user_configurable(comfy: dict) -> list[str]:
    """根据 comfy 配置推断默认的用户可编辑项。"""
    items = ["comfy_seed", "comfy_translate", "comfy_prompt"]
    if comfy.get("model_selectable", True):
        items.append("comfy_model")
    if comfy.get("width_node"):
        items.extend(["comfy_width", "comfy_height"])
    if comfy.get("output_type") == "video":
        items.extend(["comfy_video_aspect", "comfy_video_resolution", "comfy_video_frames"])
    if comfy.get("upscale_switch_node"):
        items.append("comfy_upscale_enabled")
    if comfy.get("pussydetailer_switch_node"):
        items.append("comfy_pussydetailer_enabled")
    if comfy.get("facedetailer_switch_node"):
        items.append("comfy_facedetailer_enabled")
    if comfy.get("lora_node"):
        items.append("comfy_lora_variant")
    if comfy.get("face_detailer_prompt_node"):
        items.append("comfy_face_prompt")
    if comfy.get("lora_enable_node"):
        items.extend(["comfy_krea2_lora_enabled", "comfy_krea2_lora_strength"])
    return items


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    export(dry_run=dry_run)
```

- [ ] **Step 3: 运行 dry-run 验证输出**

```bash
cd "$(dirname "$0")"
uv run python -m scripts.export_workflows --dry-run
```

Expected: 打印 9 个工作流的完整 JSON 内容，不做任何文件写入。

- [ ] **Step 4: 运行实际导出**

```bash
uv run python -m scripts.export_workflows --write
```

Expected: 创建 `data/workflows/` 和 `data/comfy_workflows/` 目录，每个工作流一个 `.json` 文件。

- [ ] **Step 5: 验证导出文件**

```bash
ls data/workflows/*.json | wc -l
ls data/comfy_workflows/*.json | wc -l
python -c "import json; [json.load(open(f)) for f in Path('data/workflows').glob('*.json')]; print('All JSON valid')"
```

Expected: 9 个 workflows 文件, 9 个 comfy_workflows 文件, 全部 JSON 合法。

- [ ] **Step 6: Commit**

```bash
git add scripts/ data/workflows/ data/comfy_workflows/
git commit -m "feat: 导出脚本 — 硬编码工作流配置迁移为 data/workflows/*.json"
```

---

### Task 2: `config.py` 动态加载工作流配置

**Files:**
- Modify: `config.py`

**Interfaces:**
- Produces: `WORKFLOW_REGISTRY` (list) 和 `COMFY_WORKFLOWS` (dict) 由 `_load_workflows()` 生成
- Consumes: `data/workflows/*.json`

- [ ] **Step 1: 在 `config.py` 文件末尾添加 `_load_workflows()` 函数并替换模块级变量**

在 `config.py` 中找到 `WORKFLOW_REGISTRY = [...]` 和 `COMFY_WORKFLOWS = {...}` 两处定义，将它们分别重命名为 `_DEFAULT_WORKFLOW_REGISTRY` 和 `_DEFAULT_COMFY_WORKFLOWS`，然后在末尾添加加载函数。

先在文件头部确认有 `import json` 和 `from pathlib import Path` 以及 `logging`:

```python
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)
```

再将原来的 `WORKFLOW_REGISTRY = [` 改为 `_DEFAULT_WORKFLOW_REGISTRY = [`，将原来的 `COMFY_WORKFLOWS = {` 改为 `_DEFAULT_COMFY_WORKFLOWS = {`。

在文件末尾（`COMFY_LORA_VARIANTS` 等定义之后、`DEFAULT_USER_SETTINGS` 之前）添加：

```python
def _load_workflows():
    """从 data/workflows/ 加载所有配置。"""
    wf_dir = Path("data/workflows")
    if not wf_dir.exists():
        return _DEFAULT_WORKFLOW_REGISTRY, _DEFAULT_COMFY_WORKFLOWS

    files = sorted(wf_dir.glob("*.json"))
    if not files:
        return _DEFAULT_WORKFLOW_REGISTRY, _DEFAULT_COMFY_WORKFLOWS

    registry = []
    comfy_workflows = {}
    had_any_file = False

    for f in files:
        had_any_file = True
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
                comfy = {
                    **data["comfy"],
                    "user_configurable": data.get("user_configurable", []),
                }
                comfy_workflows[key] = comfy

        except Exception:
            logger.warning("跳过无效配置: %s", f.name, exc_info=True)
            continue

    # 策略：目录有文件但全部无效/禁用 → warning + 返回空（不回退默认值）
    # 管理员主动禁用所有工作流 = 有意为之，不应冒默认值
    if had_any_file and not registry:
        logger.warning("启用的工作流配置文件均无法加载，返回空列表")

    return registry, comfy_workflows


WORKFLOW_REGISTRY, COMFY_WORKFLOWS = _load_workflows()
```

同时需要更新 `COMFY_DEFAULT_WORKFLOW`（如果引用了旧路径）以及任何引用 `COMFY_WORKFLOWS[key]["path"]` 的地方改为使用 `"workflow_file"` 拼路径。检查 `comfy_api.py` 中是否有 `path` 引用：

在 `comfy_api.py` 的 `_load_workflow()` 函数中，找到 `path = Path(wf_config["path"])`，改为：

```python
wf_file = wf_config.get("workflow_file", wf_config.get("path", ""))
if "/" in wf_file or "\\" in wf_file:
    path = Path(wf_file)
else:
    path = Path("data/comfy_workflows") / wf_file
```

- [ ] **Step 2: 验证 Bot 可以正常启动**

```bash
uv run python -c "
from config import WORKFLOW_REGISTRY, COMFY_WORKFLOWS
print(f'Loaded {len(WORKFLOW_REGISTRY)} workflows from:')
for wf in WORKFLOW_REGISTRY:
    key = wf['key']
    has_nodes = bool(COMFY_WORKFLOWS.get(key, {}).get('prompt_node'))
    enabled = wf.get('enabled', True)
    print(f'  {key} nodes={has_nodes}')
"
```

Expected: 加载 9 个工作流，每个都有 prompt_node。

- [ ] **Step 3: 验证 Bot 完整导入无报错**

```bash
uv run python -c "from bot import main; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: 删除 data/workflows/ 目录并验证回退**

```bash
mv data/workflows data/workflows.bak
uv run python -c "
from config import WORKFLOW_REGISTRY, COMFY_WORKFLOWS
print(f'Fallback: {len(WORKFLOW_REGISTRY)} workflows')
"
mv data/workflows.bak data/workflows
```

Expected: 回退到 9 个默认工作流。

- [ ] **Step 5: Commit**

```bash
git add config.py services/comfy_api.py
git commit -m "feat: config.py 动态加载 data/workflows/*.json，目录不存在回退默认值"
```

---

### Task 3: 阶段 2 — 只读管理页骨架 (Flask + 登录 + 列表页)

**Files:**
- Create: `admin/__init__.py`
- Create: `admin/app.py`
- Create: `admin/templates/base.html`
- Create: `admin/templates/login.html`
- Create: `admin/templates/list.html`
- Modify: `pyproject.toml` (add `[project.optional-dependencies] admin = ["flask"]`)
- Modify: `.env.example` (add ADMIN_PASSWORD, FLASK_SECRET_KEY)

**Interfaces:**
- Produces: Flask app 可启动, `/login` 可登录, `/` 展示列表

- [ ] **Step 1: 添加 Flask 依赖**

```bash
uv add --optional admin flask
```

- [ ] **Step 2: 更新 `.env.example`**

在 `.env.example` 末尾添加：

```env
# Web 管理面板认证 (Flask)
ADMIN_PASSWORD=change_me
FLASK_SECRET_KEY=change_me_to_random_string
```

- [ ] **Step 3: 创建 `admin/__init__.py`**

```bash
mkdir -p admin/templates admin/static && touch admin/__init__.py
```

- [ ] **Step 4: 创建 `admin/app.py`**

```python
"""Flask Web 管理面板 — 工作流配置管理。"""

import os
import sys
from functools import wraps

from flask import Flask, redirect, render_template, request, session, url_for

app = Flask(__name__)

PASSWORD = os.getenv("ADMIN_PASSWORD", "")
SECRET = os.getenv("FLASK_SECRET_KEY", "")

if not PASSWORD or not SECRET:
    print("ERROR: ADMIN_PASSWORD 或 FLASK_SECRET_KEY 未设置，拒绝启动", file=sys.stderr)
    sys.exit(1)

app.secret_key = SECRET
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login_page", next=request.path))
        return f(*args, **kwargs)
    return decorated


@app.route("/login", methods=["GET", "POST"])
def login_page():
    error = None
    if request.method == "POST":
        if request.form.get("password") == PASSWORD:
            session["logged_in"] = True
            next_url = request.args.get("next", url_for("list_workflows"))
            return redirect(next_url)
        error = "密码错误"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))


@app.route("/")
@login_required
def list_workflows():
    workflows = _load_all_workflows()
    return render_template("list.html", workflows=workflows)


def _load_all_workflows() -> list[dict]:
    """加载所有工作流配置文件（含被禁用的）。"""
    import json
    from pathlib import Path

    wf_dir = Path("data/workflows")
    if not wf_dir.exists():
        return []

    result = []
    for f in sorted(wf_dir.glob("*.json")):
        try:
            with open(f, encoding="utf-8") as fp:
                data = json.load(fp)
            data["_filename"] = f.name
            result.append(data)
        except Exception:
            pass
    return result


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
```

- [ ] **Step 5: 创建 `admin/templates/base.html`**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}SD Bot Admin{% endblock %}</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #f5f5f5; color: #333; padding: 20px; }
        nav { margin-bottom: 20px; padding: 10px 0; border-bottom: 1px solid #ddd; }
        nav a { margin-right: 15px; text-decoration: none; color: #0066cc; }
        .container { max-width: 900px; margin: 0 auto; }
        table { width: 100%; border-collapse: collapse; background: #fff; border-radius: 6px; overflow: hidden; }
        th, td { padding: 10px 15px; text-align: left; border-bottom: 1px solid #eee; }
        th { background: #f8f8f8; }
        .btn { display: inline-block; padding: 6px 14px; border-radius: 4px; text-decoration: none; color: #fff; font-size: 14px; }
        .btn-primary { background: #0066cc; }
        .btn-danger { background: #cc3300; }
        .btn-warning { background: #cc9900; }
        .btn-success { background: #33aa33; }
        .badge { padding: 2px 8px; border-radius: 10px; font-size: 12px; }
        .badge-on { background: #d4edda; color: #155724; }
        .badge-off { background: #f8d7da; color: #721c24; }
    </style>
    {% block head %}{% endblock %}
</head>
<body>
    <div class="container">
        {% if session.get("logged_in") %}
        <nav>
            <a href="/">工作流列表</a>
            <a href="/new">新建工作流</a>
            <a href="/logout" style="float:right">退出</a>
        </nav>
        {% endif %}
        {% block content %}{% endblock %}
    </div>
</body>
</html>
```

- [ ] **Step 6: 创建 `admin/templates/login.html`**

```html
{% extends "base.html" %}
{% block title %}登录{% endblock %}
{% block content %}
<h2>管理员登录</h2>
<form method="POST" style="margin-top:20px; max-width:300px;">
    <input type="password" name="password" placeholder="密码" required
           style="width:100%; padding:8px; margin-bottom:10px; border:1px solid #ddd; border-radius:4px;">
    {% if error %}<p style="color:red;">{{ error }}</p>{% endif %}
    <button type="submit" class="btn btn-primary" style="width:100%">登录</button>
</form>
{% endblock %}
```

- [ ] **Step 7: 创建 `admin/templates/list.html`**

```html
{% extends "base.html" %}
{% block title %}工作流列表{% endblock %}
{% block content %}
<h2>工作流配置</h2>
<table style="margin-top:10px;">
    <thead>
        <tr>
            <th>Key</th>
            <th>菜单名称</th>
            <th>类型</th>
            <th>状态</th>
            <th>操作</th>
        </tr>
    </thead>
    <tbody>
    {% for wf in workflows %}
        <tr>
            <td><code>{{ wf.key }}</code></td>
            <td>{{ wf.menu.label if wf.menu else "?" }}</td>
            <td>{{ wf.menu.input_type if wf.menu else "?" }}</td>
            <td>
                {% if wf.enabled %}
                <span class="badge badge-on">启用</span>
                {% else %}
                <span class="badge badge-off">禁用</span>
                {% endif %}
            </td>
            <td>
                <a href="/detail/{{ wf.key }}" class="btn btn-primary">详情</a>
                <a href="/edit/{{ wf.key }}" class="btn btn-warning">编辑</a>
                <form method="POST" action="/disable/{{ wf.key }}" style="display:inline">
                    <button type="submit" class="btn btn-danger" style="font-size:14px;">禁用</button>
                </form>
            </td>
        </tr>
    {% endfor %}
    {% if not workflows %}
        <tr><td colspan="5" style="text-align:center; padding:30px;">暂无工作流配置</td></tr>
    {% endif %}
    </tbody>
</table>
{% endblock %}
```

- [ ] **Step 8: 验证管理页启动**

```bash
ADMIN_PASSWORD=test FLASK_SECRET_KEY=test uv run python -m admin.app &
sleep 2
curl -s http://localhost:8080/login | head -5
kill %1
```

Expected: HTML 登录页返回。

- [ ] **Step 9: 验证未设置密码时拒绝启动**

```bash
ADMIN_PASSWORD="" FLASK_SECRET_KEY="" uv run python -m admin.app
echo "Exit code: $?"
```

Expected: `Exit code: 1` + 错误消息。

- [ ] **Step 10: Commit**

```bash
git add admin/ pyproject.toml uv.lock .env.example
git commit -m "feat: Flask 只读管理页 — 登录 + 工作流列表"
```

> **路径常量**: 实施时所有 `Path("data/workflows")` / `Path("data/comfy_workflows")` 统一通过 `admin/` 模块级常量引用（如 `WORKFLOW_DIR = Path(os.getenv("DATA_DIR", "data")) / "workflows"`），避免散落字面量。

---

### Task 4: 阶段 2 — 详情页 + 节点校验

**Files:**
- Modify: `admin/app.py` (add `/detail/<key>` route)
- Create: `admin/validators.py`
- Create: `admin/templates/detail.html`

**Interfaces:**
- Consumes: `_load_all_workflows()` from Task 3
- Produces: `/detail/<key>` page with validation report

- [ ] **Step 1: 创建 `admin/validators.py`**

```python
"""校验工作流配置与 ComfyUI workflow JSON 的匹配性。"""

import json
from pathlib import Path

COMFY_DIR = Path("data/comfy_workflows")

NODE_FIELDS = [
    "prompt_node", "seed_node", "model_node",
    "width_node", "height_node",
    "video_width_node", "video_height_node", "video_frames_node",
    "load_image_node",
    "upscale_switch_node", "pussydetailer_switch_node",
    "facedetailer_switch_node",
    "sd_upscale_node", "sd_upscale_prompt_node",
    "lora_node", "lora_enable_node", "lora_strength_node",
    "detailer_prompt_node", "face_detailer_prompt_node",
    "facedetailer_seed_node",
]


def validate_workflow_file(name: str) -> str | None:
    """校验 workflow_file 不包含路径穿越。返回错误消息或 None。"""
    p = Path(name)
    if p.name != name or ".." in p.parts:
        return "workflow_file 只能填写文件名，不允许路径"
    path = COMFY_DIR / name
    if not path.exists():
        return f"文件不存在: {name}"
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return "workflow JSON 顶层必须是 dict"
    except json.JSONDecodeError as e:
        return f"workflow JSON 无效: {e}"
    except Exception as e:
        return f"读取 workflow 文件失败: {e}"
    return None


def validate_nodes(comfy_cfg: dict) -> list[dict]:
    """校验节点映射。返回校验报告列表。"""
    name = comfy_cfg.get("workflow_file", "")
    if not name:
        return [{"field": "workflow_file", "status": "error", "msg": "未设置"}]

    path = COMFY_DIR / name
    try:
        with open(path, encoding="utf-8") as f:
            wf_json = json.load(f)
    except Exception:
        return [{"field": "workflow_file", "status": "error", "msg": "无法读取"}]

    report = _check_nodes(comfy_cfg, wf_json)
    _check_model_class(comfy_cfg, wf_json, report)
    _check_load_image_nodes(comfy_cfg, wf_json, report)
    return report


def _check_nodes(comfy_cfg: dict, wf_json: dict, report: list = None) -> list:
    if report is None:
        report = []

    key_suffix_map = {
        "prompt_node": "prompt_key",
        "seed_node": "seed_key",
        "model_node": "model_key",
        "width_node": "width_key",
        "height_node": "height_key",
        "video_width_node": "video_width_key",
        "video_height_node": "video_height_key",
        "video_frames_node": "video_frames_key",
        "load_image_node": "load_image_key",
        "upscale_switch_node": "upscale_switch_key",
        "sd_upscale_node": "sd_upscale_seed_key",
        "sd_upscale_prompt_node": "sd_upscale_prompt_key",
        "lora_strength_node": "lora_strength_key",
        "lora_enable_node": "lora_enable_key",
        "detailer_prompt_node": "detailer_prompt_key",
        "face_detailer_prompt_node": "face_detailer_prompt_key",
        "facedetailer_seed_node": "facedetailer_seed_key",
    }

    checked_nodes = set()

    for node_field in NODE_FIELDS:
        value = comfy_cfg.get(node_field)
        if value is None:
            continue

        nodes = value if isinstance(value, list) else [str(value)]
        key_field = key_suffix_map.get(node_field)
        key_value = comfy_cfg.get(key_field) if key_field else None

        for nid in nodes:
            if not nid:
                continue
            nid = str(nid)
            if nid in checked_nodes:
                continue
            checked_nodes.add(nid)

            node = wf_json.get(nid)
            if node is None:
                report.append({"field": node_field, "node": nid, "status": "error",
                               "msg": f"节点 {nid} 不存在"})
                continue

            class_type = node.get("class_type", "?")
            if key_value and key_value not in node.get("inputs", {}):
                report.append({"field": key_field or node_field, "node": nid,
                               "status": "error",
                               "msg": f"key '{key_value}' 不在节点 {nid} 的 inputs 中"
                                      f" (可用: {list(node.get('inputs', {}).keys())})"})
            else:
                report.append({"field": node_field, "node": nid, "status": "ok",
                               "msg": f"节点 {nid} ({class_type}) 校验通过"})

    return report


def _check_model_class(comfy_cfg: dict, wf_json: dict, report: list) -> None:
    expected = comfy_cfg.get("model_loader_class")
    if not expected:
        return

    model_node = comfy_cfg.get("model_node")
    if not model_node:
        return

    nodes = model_node if isinstance(model_node, list) else [str(model_node)]
    for nid in nodes:
        node = wf_json.get(str(nid))
        if node and node.get("class_type") != expected:
            report.append({"field": "model_loader_class", "node": nid, "status": "error",
                           "msg": f"class_type 不匹配: 期望 {expected}, 实际 {node.get('class_type')}"})


def _check_load_image_nodes(comfy_cfg: dict, wf_json: dict, report: list) -> None:
    img_nodes = comfy_cfg.get("load_image_nodes")
    if not img_nodes or not isinstance(img_nodes, dict):
        return

    for role, cfg in img_nodes.items():
        if not isinstance(cfg, dict):
            continue
        nid = str(cfg.get("node", ""))
        key = cfg.get("key", "")
        node = wf_json.get(nid)
        if not node:
            report.append({"field": "load_image_nodes", "node": nid, "status": "error",
                           "msg": f"load_image_nodes.{role}: 节点 {nid} 不存在"})
        elif key not in node.get("inputs", {}):
            report.append({"field": "load_image_nodes", "node": nid, "status": "error",
                           "msg": f"load_image_nodes.{role}: key '{key}' 不在 inputs 中"})
        else:
            report.append({"field": "load_image_nodes", "node": nid, "status": "ok",
                           "msg": f"load_image_nodes.{role}: 节点 {nid} 校验通过"})
```

- [ ] **Step 2: 在 `admin/app.py` 中添加 `/detail/<key>` 路由**

在 `list_workflows()` 之后添加：

```python
@app.route("/detail/<key>")
@login_required
def detail_workflow(key: str):
    import json
    from pathlib import Path

    path = Path("data/workflows") / f"{key}.json"
    if not path.exists():
        return "工作流不存在", 404

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    raw_json = json.dumps(data, ensure_ascii=False, indent=2)

    from admin.validators import validate_workflow_file, validate_nodes

    file_error = None
    node_report = []
    if data.get("comfy"):
        wf_name = data["comfy"].get("workflow_file", "")
        if wf_name:
            file_error = validate_workflow_file(wf_name)
            if file_error is None:
                node_report = validate_nodes(data["comfy"])

    return render_template("detail.html",
                           wf=data, raw_json=raw_json,
                           file_error=file_error, node_report=node_report)
```

- [ ] **Step 3: 创建 `admin/templates/detail.html`**

```html
{% extends "base.html" %}
{% block title %}详情 — {{ wf.key }}{% endblock %}
{% block content %}
<h2>{{ wf.menu.emoji }} {{ wf.menu.label }}</h2>
<p>{{ wf.menu.description }}</p>

<h3 style="margin-top:20px;">基本信息</h3>
<table>
    <tr><td>Key</td><td><code>{{ wf.key }}</code></td></tr>
    <tr><td>状态</td><td>{{ "启用" if wf.enabled else "禁用" }}</td></tr>
    <tr><td>输入类型</td><td>{{ wf.menu.input_type }}</td></tr>
    <tr><td>后端</td><td>{{ wf.menu.backend }}</td></tr>
    <tr><td>Workflow 文件</td><td><code>{{ wf.comfy.workflow_file }}</code></td></tr>
    <tr><td>用户可配置项</td><td>{{ wf.user_configurable|join(", ") }}</td></tr>
</table>

<h3 style="margin-top:20px;">校验报告</h3>
{% if file_error %}
    <p style="color:red;">⚠️ workflow 文件错误: {{ file_error }}</p>
{% else %}
    <table>
        <thead><tr><th>字段</th><th>节点</th><th>状态</th><th>详情</th></tr></thead>
        <tbody>
        {% for r in node_report %}
            <tr>
                <td><code>{{ r.field }}</code></td>
                <td><code>{{ r.node }}</code></td>
                <td style="color:{% if r.status == 'error' %}red{% else %}green{% endif %};">
                    {{ r.status }}
                </td>
                <td>{{ r.msg }}</td>
            </tr>
        {% endfor %}
        {% if not node_report %}
            <tr><td colspan="4">无节点配置</td></tr>
        {% endif %}
        </tbody>
    </table>
{% endif %}

<h3 style="margin-top:20px;">原始 JSON</h3>
<pre style="background:#f8f8f8; padding:15px; border-radius:4px; overflow-x:auto; max-height:400px;">{{ raw_json }}</pre>

<a href="/" class="btn btn-primary" style="margin-top:20px;">返回列表</a>
{% endblock %}
```

- [ ] **Step 4: 验证详情页**

```bash
ADMIN_PASSWORD=test FLASK_SECRET_KEY=test uv run python -m admin.app &
sleep 2
# 登录并访问详情页
curl -s -c /tmp/admin_cookie -X POST http://localhost:8080/login \
  -d "password=test" -o /dev/null
curl -s -b /tmp/admin_cookie http://localhost:8080/detail/z-image-turbo | grep -c "校验报告"
kill %1
```

Expected: 返回包含"校验报告"的 HTML。

- [ ] **Step 5: Commit**

```bash
git add admin/
git commit -m "feat: 管理页详情页 + 节点校验报告"
```

---

### Task 5: 阶段 3 — 新建/编辑表单 + 软禁用/归档

**Files:**
- Modify: `admin/app.py` (add `/new`, `/edit/<key>`, `/disable/<key>`, `/enable/<key>`, `/archive/<key>` routes)
- Create: `admin/workflow_store.py`
- Create: `admin/templates/form.html`

**Interfaces:**
- Produces: 新建/编辑页面, 禁用/启用/归档操作
- Consumes: `validators.py` from Task 4

- [ ] **Step 1: 创建 `admin/workflow_store.py`**

```python
"""读写 workflow 配置文件，支持原子写入和软操作。"""

import json
from pathlib import Path

WORKFLOW_DIR = Path("data/workflows")


def load_workflow(key: str) -> dict | None:
    path = WORKFLOW_DIR / f"{key}.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_workflow(data: dict) -> None:
    key = data["key"]
    WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)
    path = WORKFLOW_DIR / f"{key}.json"
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def disable_workflow(key: str) -> None:
    data = load_workflow(key)
    if data is None:
        raise FileNotFoundError(key)
    data["enabled"] = False
    save_workflow(data)


def enable_workflow(key: str) -> None:
    data = load_workflow(key)
    if data is None:
        raise FileNotFoundError(key)
    data["enabled"] = True
    save_workflow(data)


def archive_workflow(key: str) -> None:
    src = WORKFLOW_DIR / f"{key}.json"
    if not src.exists():
        raise FileNotFoundError(key)
    trash = WORKFLOW_DIR / ".trash"
    trash.mkdir(exist_ok=True)
    src.rename(trash / f"{key}.json")


def build_comfy_from_form(form: dict) -> dict:
    """从表单数据构建 comfy 配置 dict。空值字段不包含在结果中。"""
    node_fields = [
        "prompt_node", "prompt_key", "seed_node", "seed_key",
        "model_node", "model_key", "model_loader_class",
        "width_node", "width_key", "height_node", "height_key",
        "video_width_node", "video_width_key", "video_height_node",
        "video_height_key", "video_frames_node", "video_frames_key",
        "load_image_node", "load_image_key",
        "upscale_switch_node", "upscale_switch_key",
        "upscale_switch_on", "upscale_switch_off",
        "pussydetailer_switch_node", "pussydetailer_switch_key",
        "facedetailer_switch_node", "facedetailer_switch_key",
        "facedetailer_switch_on", "facedetailer_switch_off",
        "sd_upscale_node", "sd_upscale_seed_key",
        "sd_upscale_prompt_node", "sd_upscale_prompt_key",
        "lora_node", "lora_enable_node", "lora_enable_key",
        "lora_strength_node", "lora_strength_key",
        "detailer_prompt_node", "detailer_prompt_key",
        "face_detailer_prompt_node", "face_detailer_prompt_key",
        "facedetailer_seed_node", "facedetailer_seed_key",
        "default_model",
    ]

    comfy = {}
    for field in node_fields:
        val = form.get(field, "").strip()
        if val:
            comfy[field] = val
    return comfy
```

- [ ] **Step 2: 在 `admin/app.py` 中添加 CRUD 路由**

在 `detail_workflow()` 之后添加：

```python
@app.route("/new", methods=["GET", "POST"])
@login_required
def new_workflow():
    import re
    from admin.workflow_store import save_workflow, load_workflow, build_comfy_from_form

    error = None
    if request.method == "POST":
        key = request.form.get("key", "").strip()
        if not re.fullmatch(r"[a-z0-9_-]+", key):
            error = "key 只能包含小写字母、数字、短横线、下划线"
        elif load_workflow(key):
            error = f"工作流 '{key}' 已存在"
        else:
            # 基础校验（menu 必填字段 + workflow_file + input_type 白名单）
            error = _validate_form(request.form)
        if not error:
            comfy = build_comfy_from_form(request.form)
            comfy["workflow_file"] = request.form.get("workflow_file", "").strip()
            comfy["is_img2img"] = request.form.get("is_img2img") == "true"
            comfy["label"] = request.form.get("comfy_label", "").strip()
            comfy["model_selectable"] = request.form.get("model_selectable") == "true"

            uc = request.form.getlist("user_configurable")
            data = {
                "schema_version": 1,
                "key": key,
                "enabled": True,
                "menu": {
                    "emoji": request.form.get("menu_emoji", "").strip(),
                    "label": request.form.get("menu_label", "").strip(),
                    "description": request.form.get("menu_description", "").strip(),
                    "how_to": request.form.get("menu_how_to", "").strip(),
                    "input_type": request.form.get("menu_input_type", "text"),
                    "backend": "comfyui",
                },
                "comfy": comfy,
                "user_configurable": uc,
            }
            save_workflow(data)
            return redirect(url_for("detail_workflow", key=key))
    return render_template("form.html", wf=None, error=error,
                           uc_options=_ALL_UC_OPTIONS)


def _validate_form(form) -> str | None:
    """返回错误消息或 None。"""
    if not form.get("menu_label", "").strip():
        return "菜单名称不能为空"
    if not form.get("menu_description", "").strip():
        return "描述不能为空"
    if form.get("menu_input_type", "text") not in ("text", "photo"):
        return "输入类型只能是 text 或 photo"
    wf_file = form.get("workflow_file", "").strip()
    if not wf_file:
        return "workflow_file 不能为空"
    if "/" in wf_file or "\\" in wf_file or ".." in wf_file:
        return "workflow_file 不允许路径"
    from pathlib import Path
    if not (Path("data/comfy_workflows") / wf_file).exists():
        return f"workflow 文件不存在: {wf_file}"
    return None


@app.route("/edit/<key>", methods=["GET", "POST"])
@login_required
def edit_workflow(key: str):
    from admin.workflow_store import load_workflow, save_workflow, build_comfy_from_form

    data = load_workflow(key)
    if data is None:
        return "工作流不存在", 404

    error = None
    if request.method == "POST":
        comfy = build_comfy_from_form(request.form)
        comfy["workflow_file"] = request.form.get("workflow_file", "").strip()
        comfy["is_img2img"] = request.form.get("is_img2img") == "true"
        comfy["label"] = request.form.get("comfy_label", "").strip()
        comfy["model_selectable"] = request.form.get("model_selectable") == "true"

        uc = request.form.getlist("user_configurable")
        data["menu"] = {
            "emoji": request.form.get("menu_emoji", "").strip(),
            "label": request.form.get("menu_label", "").strip(),
            "description": request.form.get("menu_description", "").strip(),
            "how_to": request.form.get("menu_how_to", "").strip(),
            "input_type": request.form.get("menu_input_type", "text"),
            "backend": "comfyui",
        }
        data["comfy"] = comfy
        data["user_configurable"] = uc
        save_workflow(data)
        return redirect(url_for("detail_workflow", key=key))

    return render_template("form.html", wf=data, error=error,
                           uc_options=_ALL_UC_OPTIONS)


ALL_UC_OPTIONS = [
    "comfy_model", "comfy_seed", "comfy_width", "comfy_height",
    "comfy_translate", "comfy_upscale_enabled", "comfy_pussydetailer_enabled",
    "comfy_facedetailer_enabled", "comfy_lora_variant", "comfy_face_prompt",
    "comfy_prompt", "comfy_video_aspect", "comfy_video_resolution",
    "comfy_video_frames", "comfy_krea2_lora_enabled", "comfy_krea2_lora_strength",
]
_ALL_UC_OPTIONS = ALL_UC_OPTIONS


@app.route("/disable/<key>", methods=["POST"])
@login_required
def disable_handler(key: str):
    from admin.workflow_store import disable_workflow
    disable_workflow(key)
    return redirect(url_for("list_workflows"))


@app.route("/enable/<key>", methods=["POST"])
@login_required
def enable_handler(key: str):
    from admin.workflow_store import enable_workflow
    enable_workflow(key)
    return redirect(url_for("list_workflows"))


@app.route("/archive/<key>", methods=["POST"])
@login_required
def archive_handler(key: str):
    from admin.workflow_store import archive_workflow
    archive_workflow(key)
    return redirect(url_for("list_workflows"))
```

- [ ] **Step 3: 创建 `admin/templates/form.html`**

```html
{% extends "base.html" %}
{% block title %}{{ "编辑" if wf else "新建" }}工作流{% endblock %}
{% block content %}
<h2>{{ "编辑" if wf else "新建" }}工作流</h2>

{% if error %}<p style="color:red;">{{ error }}</p>{% endif %}

<form method="POST">
    <fieldset style="margin-bottom:20px; padding:15px; border:1px solid #ddd; border-radius:6px;">
        <legend>注册信息</legend>
        <label>Key <input name="key" value="{{ wf.key if wf else '' }}"
               {% if wf %}readonly{% endif %} required pattern="[a-z0-9_-]+"
               style="width:200px;"></label><br><br>
        <label>Emoji <input name="menu_emoji" value="{{ wf.menu.emoji if wf else '' }}"
               style="width:80px;"></label>
        <label>菜单名称 <input name="menu_label" value="{{ wf.menu.label if wf else '' }}"
               required style="width:300px;"></label><br><br>
        <label>描述 <input name="menu_description" value="{{ wf.menu.description if wf else '' }}"
               required style="width:500px;"></label><br><br>
        <label>使用说明<br>
               <textarea name="menu_how_to" rows="3" style="width:500px;">{{ wf.menu.how_to if wf else '' }}</textarea>
        </label><br><br>
        <label>输入类型
            <select name="menu_input_type">
                <option value="text" {% if wf and wf.menu.input_type=='text' %}selected{% endif %}>text (文生图)</option>
                <option value="photo" {% if wf and wf.menu.input_type=='photo' %}selected{% endif %}>photo (图生图)</option>
            </select>
        </label>
    </fieldset>

    <fieldset style="margin-bottom:20px; padding:15px; border:1px solid #ddd; border-radius:6px;">
        <legend>节点映射 (ComfyUI)</legend>
        <label>Workflow 文件 <input name="workflow_file"
               value="{{ wf.comfy.workflow_file if wf else '' }}" style="width:300px;"></label>
        <span style="font-size:12px;color:#888;">文件名，如 zit-up-pussy-face.json</span><br><br>

        <label>ComfyUI Label <input name="comfy_label"
               value="{{ wf.comfy.label if wf else '' }}" style="width:300px;"></label><br><br>

        <label>prompt_node <input name="prompt_node"
               value="{{ wf.comfy.prompt_node if wf else '' }}" style="width:100px;"></label>
        <label>prompt_key <input name="prompt_key"
               value="{{ wf.comfy.prompt_key if wf else '' }}" style="width:100px;"></label><br><br>

        <label>seed_node <input name="seed_node"
               value="{{ wf.comfy.seed_node if wf else '' }}" style="width:100px;"></label>
        <label>seed_key <input name="seed_key"
               value="{{ wf.comfy.seed_key if wf else '' }}" style="width:100px;"></label><br><br>

        <label>model_node <input name="model_node"
               value="{{ wf.comfy.model_node if wf else '' }}" style="width:100px;"></label>
        <label>model_key <input name="model_key"
               value="{{ wf.comfy.model_key if wf else '' }}" style="width:100px;"></label>
        <label>model_loader_class <input name="model_loader_class"
               value="{{ wf.comfy.model_loader_class if wf else '' }}" style="width:150px;"></label><br><br>

        <label>width_node <input name="width_node"
               value="{{ wf.comfy.width_node if wf else '' }}" style="width:80px;"></label>
        <label>width_key <input name="width_key"
               value="{{ wf.comfy.width_key if wf else '' }}" style="width:80px;"></label>
        <label>height_node <input name="height_node"
               value="{{ wf.comfy.height_node if wf else '' }}" style="width:80px;"></label>
        <label>height_key <input name="height_key"
               value="{{ wf.comfy.height_key if wf else '' }}" style="width:80px;"></label><br><br>

        <label>default_model <input name="default_model"
               value="{{ wf.comfy.default_model if wf else '' }}" style="width:350px;"></label><br><br>

        <label>model_selectable
            <select name="model_selectable">
                <option value="true" {% if wf and wf.comfy.model_selectable %}selected{% endif %}>true</option>
                <option value="false" {% if wf and not wf.comfy.model_selectable %}selected{% endif %}>false</option>
            </select>
        </label><br><br>

        <label>is_img2img
            <select name="is_img2img">
                <option value="false" {% if wf and not wf.comfy.is_img2img %}selected{% endif %}>false</option>
                <option value="true" {% if wf and wf.comfy.is_img2img %}selected{% endif %}>true</option>
            </select>
        </label>

        <p style="margin-top:10px;font-size:13px;color:#888;">
            其他节点字段（load_image_node、switch节点、lora节点等）请编辑 JSON 文件或后续扩展表单。
        </p>
    </fieldset>

    <fieldset style="margin-bottom:20px; padding:15px; border:1px solid #ddd; border-radius:6px;">
        <legend>用户可编辑项</legend>
        {% for opt in uc_options %}
        <label style="margin-right:15px; display:inline-block;">
            <input type="checkbox" name="user_configurable" value="{{ opt }}"
                   {% if wf and opt in wf.user_configurable %}checked{% endif %}>
            {{ opt }}
        </label>
        {% endfor %}
    </fieldset>

    <button type="submit" class="btn btn-success" style="font-size:16px;">保存</button>
    <a href="/" class="btn" style="background:#888;">取消</a>
</form>
{% endblock %}
```

- [ ] **Step 4: 更新列表页添加启用/归档操作**

在 `admin/templates/list.html` 的操作列更新, 将 disable 改为 disabled/enabled 切换:

```html
<td>
    <a href="/detail/{{ wf.key }}" class="btn btn-primary">详情</a>
    <a href="/edit/{{ wf.key }}" class="btn btn-warning">编辑</a>
    {% if wf.enabled %}
    <form method="POST" action="/disable/{{ wf.key }}" style="display:inline">
        <button type="submit" class="btn btn-danger" style="font-size:14px;">禁用</button>
    </form>
    {% else %}
    <form method="POST" action="/enable/{{ wf.key }}" style="display:inline">
        <button type="submit" class="btn btn-success" style="font-size:14px;">启用</button>
    </form>
    {% endif %}
    <form method="POST" action="/archive/{{ wf.key }}" style="display:inline"
          onsubmit="return confirm('归档后将移入 .trash/ 目录，确定？');">
        <button type="submit" class="btn btn-danger" style="font-size:12px; background:#999;">归档</button>
    </form>
</td>
```

- [ ] **Step 5: 验证 CRUD 操作**

```bash
ADMIN_PASSWORD=test FLASK_SECRET_KEY=test uv run python -m admin.app &
sleep 2
# Login
curl -s -c /tmp/admin_cookie -X POST http://localhost:8080/login \
  -d "password=test" -o /dev/null
# Create test workflow
curl -s -b /tmp/admin_cookie -X POST http://localhost:8080/new \
  -d "key=test-wf&menu_label=Test&menu_description=Desc&menu_how_to=How&menu_input_type=text" \
  -o /dev/null
# Verify created
curl -s -b /tmp/admin_cookie http://localhost:8080/detail/test-wf | grep -c "test-wf"
# Disable
curl -s -b /tmp/admin_cookie -X POST http://localhost:8080/disable/test-wf -o /dev/null
# Enable
curl -s -b /tmp/admin_cookie -X POST http://localhost:8080/enable/test-wf -o /dev/null
# Archive
curl -s -b /tmp/admin_cookie -X POST http://localhost:8080/archive/test-wf -o /dev/null
ls data/workflows/.trash/ | grep test-wf
kill %1
```

Expected: test-wf 文件移至 `.trash/`。

- [ ] **Step 6: Commit**

```bash
git add admin/
git commit -m "feat: 管理页编辑表单 + 禁用/启用/归档操作"
```

---

### Task 6: 阶段 3 — 上传 API + 校验 API

**Files:**
- Modify: `admin/app.py` (add `/api/upload-comfy-workflow`, `/api/validate-mapping`)

**Interfaces:**
- Produces: JSON API endpoints

- [ ] **Step 1: 在 `admin/app.py` 末尾添加 API 路由**

```python
@app.route("/api/upload-comfy-workflow", methods=["POST"])
@login_required
def api_upload_comfy_workflow():
    """上传原始 ComfyUI workflow JSON 到 data/comfy_workflows/。"""
    import json
    from pathlib import Path

    file = request.files.get("file")
    if not file:
        return {"error": "未选择文件"}, 400

    import re
    raw_name = file.filename or ""
    # 先校验原始文件名（Path().name 会截掉路径，无法检测 ../）
    if "/" in raw_name or "\\" in raw_name or ".." in raw_name:
        return {"error": "文件名不允许路径"}, 400

    filename_str = Path(raw_name).name
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.json", filename_str):
        return {"error": "文件名只能包含字母、数字、点、短横线、下划线，并以 .json 结尾"}, 400

    try:
        data = json.load(file)
    except json.JSONDecodeError as e:
        return {"error": f"JSON 无效: {e}"}, 400

    if not isinstance(data, dict):
        return {"error": "workflow JSON 顶层必须是 dict"}, 400

    dest_dir = Path("data/comfy_workflows")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename_str
    tmp = dest.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(dest)

    node_count = len(data)
    return {"ok": True, "filename": filename_str, "nodes": node_count}


@app.route("/api/validate-mapping", methods=["POST"])
@login_required
def api_validate_mapping():
    """根据表单提交的节点映射校验 workflow JSON。"""
    from admin.validators import validate_workflow_file, validate_nodes

    wf_file = request.form.get("workflow_file", "").strip()
    if not wf_file:
        return {"error": "workflow_file 不能为空"}, 400

    file_error = validate_workflow_file(wf_file)
    if file_error:
        return {"error": file_error}, 400

    ALLOWED_COMFY_FIELDS = {

    report = validate_nodes(comfy)
    errors = [r for r in report if r["status"] == "error"]
    return {"ok": True, "report": report, "error_count": len(errors)}
```

- [ ] **Step 2: 验证上传 API**

```bash
ADMIN_PASSWORD=test FLASK_SECRET_KEY=test uv run python -m admin.app &
sleep 2
curl -s -c /tmp/admin_cookie -X POST http://localhost:8080/login \
  -d "password=test" -o /dev/null
echo '{"1":{"class_type":"KSampler","inputs":{"seed":42}}}' > /tmp/test_wf.json
curl -s -b /tmp/admin_cookie -F "file=@/tmp/test_wf.json" \
  http://localhost:8080/api/upload-comfy-workflow
rm /tmp/test_wf.json
kill %1
```

Expected: `{"ok":true,"filename":"test_wf.json","nodes":1}`

- [ ] **Step 3: Commit**

```bash
git add admin/app.py
git commit -m "feat: 管理页上传 API + 节点校验 API"
```

---

### Task 7: Docker 配置

**Files:**
- Create: `Dockerfile.admin`
- Modify: `docker-compose.yml`, `docker-compose.linux.yml`, `docker-compose.windows.yml`

**Interfaces:**
- Produces: `sd-admin` 容器可通过 `docker compose up -d` 启动

- [ ] **Step 1: 创建 `Dockerfile.admin`**

```dockerfile
FROM python:3.12-slim

WORKDIR /app
RUN pip install uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --extra admin --no-dev

COPY . .

ENV PYTHONPATH=/app

EXPOSE 8080
CMD ["uv", "run", "python", "-m", "admin.app"]
```

- [ ] **Step 2: 更新 `docker-compose.yml` 添加 sd-admin 服务**

在 `services:` 下添加：

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

**注意：端口已在基础 `docker-compose.yml` 中定义，linux/windows override 文件无需重复。**

- [ ] **Step 3: 更新 `docker-compose.linux.yml` 添加 sd-admin 网络配置**

```yaml
services:
  sd-bot:
    network_mode: host
  sd-admin:
    network_mode: host
```

- [ ] **Step 4: 更新 `docker-compose.windows.yml` 添加 sd-admin 网络配置**

```yaml
services:
  sd-bot:
    extra_hosts:
      - "host.docker.internal:host-gateway"
    environment:
      PROXY_URL: socks5://host.docker.internal:10808
      COMFY_API_BASE: http://host.docker.internal:8188
  sd-admin:
    # 端口已在基础 docker-compose.yml 中定义，无需重复
```

- [ ] **Step 5: Commit**

```bash
git add Dockerfile.admin docker-compose.yml docker-compose.linux.yml docker-compose.windows.yml
git commit -m "feat: Docker 配置 — sd-admin 独立容器"
```

---

### Task 8: 阶段 4 — 菜单双重判断规则

**Files:**
- Modify: `handlers/comfy_settings.py` (update `_comfy_settings_menu` and `_add_middle_rows` to check `user_configurable`)

**Interfaces:**
- Consumes: `wf_config.get("user_configurable", [])`

- [ ] **Step 1: 在 `handlers/comfy_settings.py` 的 `_comfy_settings_menu` 中添加 `user_configurable` 检查**

当前 `_comfy_settings_menu` 中 `_add_dimension_rows` 只检查 `is_img2img` / `output_type`。改为同时检查 `user_configurable`。

在 `_add_dimension_rows` 函数开头添加：

```python
uc = wf_config.get("user_configurable", [])
```

视频分支改为：

```python
if is_video and {"comfy_video_aspect", "comfy_video_resolution", "comfy_video_frames"}.issubset(uc):
    # ... existing video code ...
```

尺寸分支改为同时检查 `user_configurable` 和节点能力：

```python
elif (
    not wf_config.get("is_img2img", False)
    and {"comfy_width", "comfy_height"}.issubset(uc)
    and wf_config.get("width_node") and wf_config.get("width_key")
    and wf_config.get("height_node") and wf_config.get("height_key")
):
    # ... existing size code ...
```

视频分支同理，除了 `user_configurable` 还要确认 `output_type == "video"` 且相关节点字段存在。

- [ ] **Step 2: 在 `_add_middle_rows` 中添加 `user_configurable` 检查**

每个条件块前添加 `uc` 检查。

LoRA 变体：

```python
if wf_config.get("lora_node") and "comfy_lora_variant" in uc:
```

三级开关：

```python
if wf_config.get("upscale_switch_node") and "comfy_upscale_enabled" in uc:
    # ... upscale toggle ...
if wf_config.get("pussydetailer_switch_node") and "comfy_pussydetailer_enabled" in uc:
    # ... pussydetailer toggle ...
if wf_config.get("facedetailer_switch_node") and "comfy_facedetailer_enabled" in uc:
    # ... facedetailer toggle ...
```

krea2 LoRA:

```python
if wf_config.get("lora_enable_node") and "comfy_krea2_lora_enabled" in uc:
    # ... krea2 lora code ...
```

脸部提示词：

```python
if wf_config.get("face_detailer_prompt_node") and "comfy_face_prompt" in uc:
    # ... face prompt code ...
```

模型：

```python
if model_selectable and "comfy_model" in uc:
    keyboard.append([InlineKeyboardButton("切换模型", ...)])
```

- [ ] **Step 3: 验证 Bot 导入无误**

```bash
uv run python -c "from bot import main; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add handlers/comfy_settings.py
git commit -m "feat: 菜单双重判断 — user_configurable + 节点能力共同控制显示"
```

---

### Task 9: 支持 workflow_file 并将配置引用 data/comfy_workflows/ (旧文件保留不删)

**Files:**
- Modify: `services/comfy_api.py` (`_load_workflow` 使用 `workflow_file` 优先，回退 `path`)
- Modify: `data/workflows/*.json` (已由导出脚本将 `path` 转为 `workflow_file`)
- No change: `data/*.json` 旧文件保留不删

- [ ] **Step 1: 更新 `comfy_api.py` 的 `_load_workflow` 函数**

找到 `path = Path(wf_config["path"])` 行，改为：

```python
# workflow_file: 新字段，只允许纯文件名，路径固定为 data/comfy_workflows/
if wf_config.get("workflow_file"):
    wf_file = wf_config["workflow_file"]
    if Path(wf_file).name != wf_file or "/" in wf_file or "\\" in wf_file or ".." in wf_file:
        raise ComfyWorkflowError("workflow_file 只能是文件名")
    path = Path("data/comfy_workflows") / wf_file
# path: 旧字段，仅兼容保留（导出脚本已将旧配置转为 workflow_file）
elif wf_config.get("path"):
    path = Path(wf_config["path"])
else:
    raise ComfyWorkflowError(f"Workflow '{wf_key}' 缺少 workflow_file/path")
```

- [ ] **Step 2: 验证 Bot 导入**

```bash
uv run python -c "from bot import main; print('OK')"
```

- [ ] **Step 3: 验证 validate_workflow() 通过**

```bash
uv run python -c "
from services.comfy_api import validate_workflow
validate_workflow()
print('All workflows validated OK')
"
```

- [ ] **Step 4: Commit**

```bash
git add services/comfy_api.py
git commit -m "feat: _load_workflow 支持 workflow_file (data/comfy_workflows/)"
```

---

### Task 10: 阶段 5 — 热加载 (可选, 不推荐第一版执行)

**Note:** 当前计划使用重新赋值 `_registry, _comfy_workflows = _load_from_files()` 但项目中大量代码使用 `from config import COMFY_WORKFLOWS` 直接引用模块级变量，reload 后旧引用不会更新。**第一版不实施热加载，配置变更后重启 Bot 生效。**

若后续需要热加载，推荐**方案 B（原地更新 dict/list 内容）**而非重新赋值：

**Files:**
- Create: `services/workflow_config.py`
- Modify: `config.py` (使用 `WorkflowConfig` 而非直接变量)
- Modify: `bot.py` (注册 `/reload` 命令)

**Note:** 此项为可选阶段，根据实际需求决定是否实施。第一版先接受重启生效。

- [ ] **Step 1: 创建 `services/workflow_config.py` 使用原地更新方案**

```python
"""工作流配置管理器，支持运行时热加载（原地更新 dict/list 避免已导入引用失效）。"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_registry: list = []
_comfy_workflows: dict = {}


def reload():
    """从文件重新加载并原地更新 registry 和 comfy_workflows。"""
    new_registry, new_comfy = _load_from_files()
    _registry.clear()
    _registry.extend(new_registry)
    _comfy_workflows.clear()
    _comfy_workflows.update(new_comfy)
    logger.info("热加载完成: %s workflows", len(_registry))


def get_registry() -> list:
    return _registry


def get_comfy_workflows() -> dict:
    return _comfy_workflows


def _load_from_files():
    # ... (同 config.py _load_workflows 逻辑)
```

- [ ] **Step 2: 更新 `config.py` 使用配置管理器**

将 `WORKFLOW_REGISTRY, COMFY_WORKFLOWS = _load_workflows()` 替换为通过 `services.workflow_config` 获取值。保留 `_DEFAULT_*` 常量和 `_load_workflows()` 用于 `workflow_config.init()`。

- [ ] **Step 3: 在 `bot.py` 中添加 `/reload` 命令 handler**

```python
from services import workflow_config as wf_config

async def reload_workflows(update, context):
    user = update.effective_user
    if user is None or user.id != ADMIN_USER_ID:
        await update.message.reply_text("无权限")
        return
    wf_config.reload()
    await update.message.reply_text(
        f"已重载 {len(wf_config.get_registry())} 个工作流配置。"
    )
```

- [ ] **Step 4: Commit**

```bash
git add services/workflow_config.py config.py bot.py
git commit -m "feat: 工作流热加载 — /reload 命令"
```
