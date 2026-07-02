"""Flask Web 管理面板 — 工作流配置管理。"""

import json
import logging
import os
import sys
from functools import wraps
from pathlib import Path

from urllib.parse import urlparse

from flask import Flask, redirect, render_template, request, session, url_for

logger = logging.getLogger(__name__)

from admin.paths import COMFY_WORKFLOW_DIR, WORKFLOW_DIR

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
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,  # 上传文件最大 2MB
)


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login_page", next=request.path))
        return f(*args, **kwargs)
    return decorated


def generate_csrf_token():
    if "_csrf_token" not in session:
        session["_csrf_token"] = os.urandom(24).hex()
    return session["_csrf_token"]

app.jinja_env.globals["csrf_token"] = generate_csrf_token

def require_csrf(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.method == "POST":
            token = session.get("_csrf_token")
            if not token or request.form.get("_csrf_token") != token:
                return "CSRF 校验失败", 403
        return f(*args, **kwargs)
    return decorated


@app.route("/login", methods=["GET", "POST"])
@require_csrf
def login_page():
    error = None
    if request.method == "POST":
        if request.form.get("password") == PASSWORD:
            session["logged_in"] = True
            next_url = request.args.get("next", url_for("list_workflows"))
            parsed = urlparse(next_url)
            if parsed.netloc or parsed.scheme not in ("", "http", "https"):
                next_url = url_for("list_workflows")
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
    if not WORKFLOW_DIR.exists():
        return []

    result = []
    for f in sorted(WORKFLOW_DIR.glob("*.json")):
        try:
            with open(f, encoding="utf-8") as fp:
                data = json.load(fp)
            data["_filename"] = f.name
            result.append(data)
        except Exception:
            logger.warning("Failed to load %s", f.name, exc_info=True)
    return result


@app.route("/detail/<key>")
@login_required
def detail_workflow(key: str):
    path = WORKFLOW_DIR / f"{key}.json"
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


@app.route("/new", methods=["GET", "POST"])
@login_required
@require_csrf
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
    if not (COMFY_WORKFLOW_DIR / wf_file).exists():
        return f"workflow 文件不存在: {wf_file}"
    return None


@app.route("/edit/<key>", methods=["GET", "POST"])
@login_required
@require_csrf
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
@require_csrf
def disable_handler(key: str):
    from admin.workflow_store import disable_workflow
    disable_workflow(key)
    return redirect(url_for("list_workflows"))


@app.route("/enable/<key>", methods=["POST"])
@login_required
@require_csrf
def enable_handler(key: str):
    from admin.workflow_store import enable_workflow
    enable_workflow(key)
    return redirect(url_for("list_workflows"))


@app.route("/archive/<key>", methods=["POST"])
@login_required
@require_csrf
def archive_handler(key: str):
    from admin.workflow_store import archive_workflow
    archive_workflow(key)
    return redirect(url_for("list_workflows"))


@app.route("/api/upload-comfy-workflow", methods=["POST"])
@login_required
def api_upload_comfy_workflow():
    """上传原始 ComfyUI workflow JSON 到 data/comfy_workflows/。"""
    import re

    file = request.files.get("file")
    if not file:
        return {"error": "未选择文件"}, 400

    raw_name = file.filename or ""
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

    dest_dir = COMFY_WORKFLOW_DIR
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
        "label", "is_img2img", "output_type", "model_selectable",
        "prompt_node", "prompt_key",
        "seed_node", "seed_key",
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
    }

    comfy = {
        k: v.strip()
        for k, v in request.form.items()
        if k in ALLOWED_COMFY_FIELDS and isinstance(v, str) and v.strip()
    }

    JSON_FIELDS = {"load_image_nodes", "upscale_switch_on", "upscale_switch_off",
                   "facedetailer_switch_on", "facedetailer_switch_off"}
    for k, v in list(comfy.items()):
        if k in JSON_FIELDS and isinstance(v, str) and v.strip():
            try:
                comfy[k] = json.loads(v)
            except json.JSONDecodeError:
                pass

    comfy["workflow_file"] = wf_file

    report = validate_nodes(comfy)
    errors = [r for r in report if r["status"] == "error"]
    return {"ok": True, "report": report, "error_count": len(errors)}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
