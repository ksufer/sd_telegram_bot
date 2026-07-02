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

from admin.paths import WORKFLOW_DIR

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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
