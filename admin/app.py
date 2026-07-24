"""SD Admin — FastAPI 后端。

网页端使用 Bot 的 ComfyUI 生成功能 + 工作流配置管理。
启动：python -m admin.app（uvicorn 0.0.0.0:8080，Compose 绑定 127.0.0.1 暴露）。
"""

import json
import logging
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import config
from admin import auth, store, tasks, validators
from services import comfy_api, storage

logger = logging.getLogger(__name__)

MAX_UPLOAD_SIZE = 20 * 1024 * 1024  # 与 Telegram 图片上限一致

app = FastAPI(title="SD Admin", docs_url=None, redoc_url=None)

STATIC_DIR = Path(__file__).parent / "static"

# 登录失败限流：每 IP 5 次失败后锁定 60 秒
_login_fails: dict[str, list[float]] = {}


@app.middleware("http")
async def reload_workflows_middleware(request: Request, call_next):
    """API 请求前热重载工作流配置（mtime 检测，廉价）。"""
    if request.url.path.startswith("/api"):
        config.maybe_reload_workflows()
    return await call_next(request)


# ── 认证 ──────────────────────────────────────────────────

def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@app.post("/api/login")
async def login(request: Request, response: Response):
    ip = _client_ip(request)
    fails = _login_fails.get(ip, [])
    if fails and time.monotonic() - fails[0] < 60 and len(fails) >= 5:
        raise HTTPException(status_code=429, detail="尝试次数过多，请 1 分钟后再试")

    body = await request.json()
    if body.get("password") != auth.ADMIN_PASSWORD:
        fails.append(time.monotonic())
        _login_fails[ip] = [t for t in fails if time.monotonic() - t < 60]
        logger.warning("登录失败: ip=%s", ip)
        raise HTTPException(status_code=401, detail="密码错误")

    _login_fails.pop(ip, None)
    cookie_value, csrf = auth.create_session()
    response.set_cookie(auth.COOKIE_NAME, cookie_value,
                        max_age=auth.COOKIE_MAX_AGE, httponly=True,
                        samesite="lax")
    return {"ok": True, "csrf": csrf}


@app.get("/api/me")
async def me(request: Request):
    csrf = auth.require_auth(request)
    return {"ok": True, "csrf": csrf}


@app.post("/api/logout")
async def logout(request: Request, response: Response):
    auth.require_auth(request)
    response.delete_cookie(auth.COOKIE_NAME)
    return {"ok": True}


# ── 工作流（生成页数据 + 管理） ──────────────────────────────

@app.get("/api/workflows")
async def list_workflows(request: Request):
    """生成页用：已启用工作流及能力描述（来自热重载后的 config）。"""
    auth.require_auth(request)
    comfy = config.COMFY_WORKFLOWS
    items = []
    for entry in config.WORKFLOW_REGISTRY:
        key = entry["key"]
        wf = comfy.get(key, {})
        items.append({
            "key": key,
            "emoji": entry.get("emoji", ""),
            "label": entry.get("label", key),
            "description": entry.get("description", ""),
            "how_to": entry.get("how_to", ""),
            "input_type": entry.get("input_type", "text"),
            "output_type": wf.get("output_type", "image"),
            "user_configurable": wf.get("user_configurable", []),
            "load_image_roles": list(wf.get("load_image_nodes", {}).keys()),
            "model_selectable": wf.get("model_selectable", True),
        })
    return {"workflows": items}


@app.get("/api/manage/workflows")
async def manage_list(request: Request):
    """管理页用：磁盘上全部配置（含禁用）。"""
    auth.require_auth(request)
    return {"workflows": store.list_workflow_configs()}


@app.get("/api/workflows/{key}/config")
async def get_config(key: str, request: Request):
    auth.require_auth(request)
    try:
        return store.load_workflow_config(key)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"配置不存在: {key}")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.put("/api/workflows/{key}/config")
async def put_config(key: str, request: Request):
    """保存原始 JSON（结构错误拒绝），返回节点校验报告。"""
    auth.require_csrf(request)
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON 解析失败")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="配置顶层必须是 JSON object")
    try:
        store.save_workflow_config(key, data)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    config.maybe_reload_workflows()
    report = validators.validate_nodes(data.get("comfy", {})) if data.get("comfy") else []
    return {"ok": True, "report": report}


@app.post("/api/workflows")
async def create_workflow(request: Request):
    auth.require_csrf(request)
    body = await request.json()
    key = str(body.get("key", "")).strip()
    try:
        store.create_workflow_config(key)
    except FileExistsError:
        raise HTTPException(status_code=409, detail=f"配置已存在: {key}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "key": key}


@app.post("/api/workflows/{key}/enable")
async def enable_workflow(key: str, request: Request):
    auth.require_csrf(request)
    try:
        store.set_workflow_enabled(key, True)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"配置不存在: {key}")
    return {"ok": True}


@app.post("/api/workflows/{key}/disable")
async def disable_workflow(key: str, request: Request):
    auth.require_csrf(request)
    try:
        store.set_workflow_enabled(key, False)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"配置不存在: {key}")
    return {"ok": True}


@app.post("/api/workflows/{key}/archive")
async def archive_workflow(key: str, request: Request):
    auth.require_csrf(request)
    try:
        store.archive_workflow(key)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"配置不存在: {key}")
    return {"ok": True}


@app.post("/api/comfy-upload")
async def comfy_upload(request: Request):
    auth.require_csrf(request)
    form = await request.form()
    file = form.get("file")
    if not isinstance(file, UploadFile) or not file.filename:
        raise HTTPException(status_code=400, detail="缺少文件")
    content = await file.read()
    if len(content) > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件过大（上限 2MB）")
    try:
        name = store.save_comfy_upload(file.filename, content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "filename": name}


@app.post("/api/validate")
async def validate_config(request: Request):
    """校验配置（不保存）：结构 + 节点映射报告。"""
    auth.require_csrf(request)
    data = await request.json()
    comfy = data.get("comfy", {}) if isinstance(data, dict) else {}
    return {"report": validators.validate_nodes(comfy)}


# ── 生成 ──────────────────────────────────────────────────

@app.get("/api/models/{wf_key}")
async def get_models(wf_key: str, request: Request):
    auth.require_auth(request)
    try:
        models = await comfy_api.get_models({"comfy_workflow": wf_key})
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"获取模型列表失败: {e}")
    return {"models": models}


@app.get("/api/settings")
async def get_settings(request: Request):
    auth.require_auth(request)
    return storage.load("_web", config.DEFAULT_USER_SETTINGS)


@app.put("/api/settings")
async def put_settings(request: Request):
    auth.require_csrf(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="设置必须是 JSON object")
    settings = storage.load("_web", config.DEFAULT_USER_SETTINGS)
    for k, v in body.items():
        if k in config.DEFAULT_USER_SETTINGS:  # 只接受已知键，防注入内部字段
            settings[k] = v
    storage.save("_web", settings)
    return {"ok": True}


@app.post("/api/generate")
async def generate(request: Request):
    auth.require_csrf(request)
    form = await request.form()
    wf_key = str(form.get("wf_key", ""))
    prompt = str(form.get("prompt", ""))
    try:
        user_settings = json.loads(str(form.get("settings", "{}")))
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="settings JSON 无效")
    if not isinstance(user_settings, dict):
        raise HTTPException(status_code=400, detail="settings 必须是 JSON object")

    wf_config = config.COMFY_WORKFLOWS.get(wf_key)
    if not wf_config:
        raise HTTPException(status_code=404, detail=f"工作流不存在或未启用: {wf_key}")

    images: dict[str, bytes] = {}
    for field, value in form.items():
        if isinstance(value, UploadFile) and value.filename:
            content = await value.read()
            if len(content) > MAX_UPLOAD_SIZE:
                raise HTTPException(status_code=400,
                                    detail=f"图片 {value.filename} 超过 20MB 上限")
            images[field] = content

    is_img2img = wf_config.get("is_img2img", False)
    if is_img2img and not images:
        raise HTTPException(status_code=400, detail="该工作流需要上传图片")
    if not is_img2img and not prompt.strip():
        raise HTTPException(status_code=400, detail="提示词不能为空")

    settings = storage.load("_web", config.DEFAULT_USER_SETTINGS)
    for k, v in user_settings.items():
        if k in config.DEFAULT_USER_SETTINGS:
            settings[k] = v
    settings["comfy_workflow"] = wf_key

    task = await tasks.create_task(wf_key, prompt.strip(), settings, images)
    return task


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str, request: Request):
    auth.require_auth(request)
    task = tasks.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


# ── 历史 ──────────────────────────────────────────────────

_MEDIA_TYPES = {
    ".png": "image/png", ".webp": "image/webp", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".mp4": "video/mp4", ".webm": "video/webm",
}


@app.get("/api/history")
async def list_history(request: Request):
    auth.require_auth(request)
    return {"items": store.list_history()}


@app.get("/api/history/{result_id}/file")
async def history_file(result_id: str, request: Request):
    auth.require_auth(request)
    path = store.get_history_file(result_id)
    if not path:
        raise HTTPException(status_code=404, detail="结果不存在")
    media_type = _MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media_type)


@app.delete("/api/history/{result_id}")
async def delete_history(result_id: str, request: Request):
    auth.require_csrf(request)
    if not store.delete_history(result_id):
        raise HTTPException(status_code=404, detail="结果不存在")
    return {"ok": True}


# ── 静态 SPA ──────────────────────────────────────────────

app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    auth.check_config()
    uvicorn.run("admin.app:app", host="0.0.0.0", port=8080, log_level="info")


if __name__ == "__main__":
    main()
