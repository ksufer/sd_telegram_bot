# AGENTS.md

为 OpenCode 等 AI 编码助手提供的高信号操作指引。详细架构和部署说明见 `CLAUDE.md`。

## 命令

```bash
uv run python bot.py    # 启动 Bot（唯一入口，不要用 main.py）
uv run python -m admin.app  # 启动 Admin 面板（FastAPI，:8080）
uv add <package>        # 添加依赖
```

- **没有**测试、lint、typecheck 配置，不要试图运行这些命令。
- Python 3.12，uv 管理依赖，lockfile 为 `uv.lock`。

## 架构要点

- `bot.py` 是唯一入口（`main.py` 是占位模板，忽略它）。
- `concurrent_updates(False)` — Bot 串行处理消息，无需担心并发。
- Handler 注册顺序决定匹配优先级（`bot.py:61-65`）：workflow_menu → settings → generation → credits → comfy_settings。
- 工作流系统由 `config.py` 中 `WORKFLOW_REGISTRY` 驱动主菜单，每个工作流关联 ComfyUI workflow JSON。
- 用户设置和额度数据持久化到 `data/user_settings/` 和 `data/credits/` 下的 JSON 文件（非内存）。
- 多步交互（种子输入、Prompt 输入、首尾帧收集）通过 `context.user_data["_waiting_*"]` 标记实现。
- 权限控制：`handlers/__init__.py` 提供 `is_authorized()`、`auth_callback` 装饰器、`_user_auth_filter()`。管理员无需在白名单中（filter 层也会自动并入）。`ALLOWED_USER_IDS` / `ALLOWED_CHAT_IDS` / `ADMIN_USER_ID` 均可从 .env 读取（逗号分隔，留空不限制）。
- 工作流配置热重载：`data/workflows/*.json` 变化时由 `config.maybe_reload_workflows()` 在消息/菜单入口自动原地重载；`comfy_api._load_workflow()` 缓存按文件 mtime 失效。管理面板改动无需重启 Bot。
- 日志：`services/logger.py` 将 httpx/httpcore 降为 WARNING（INFO 级 URL 含 bot token，且轮询噪音大）。

### 新增模块

| 模块 | 职责 |
|------|------|
| `handlers/common.py` | 共享工具函数（`safe_answer`、`reply_menu`、`get_user_id`、`refresh_workflows`） |
| `ui/keyboards.py` | 无副作用键盘构建模块，纯函数返回 `InlineKeyboardMarkup` |

### 生成流程

- `handlers/generation.py` 中 `handle_text()` / `handle_photo()` 通过 5 个辅助函数（`_check_and_charge_credit`、`_create_status_message`、`_download_tg_photo`、`_upload_to_comfy`、`_enqueue_and_notify`）消除重复代码，退款统一在调用方处理。
- `services/queue.py` 中 `_process_task()` 已拆分为 `_translate_prompt`、`_generate`、`_send_result`、`_cache_gen_context` 私有方法。
- `services/comfy_api.py` 中 `_build_payload()` 已拆分为 8 个 `_apply_*` 函数。

### Admin 面板（FastAPI + vanilla SPA）

- `admin/app.py` — FastAPI 路由（`/api/*` + 静态 SPA）；`python -m admin.app` 以 uvicorn 起在 8080。
- `admin/auth.py` — 密码登录（`ADMIN_PASSWORD`）+ HMAC 签名 cookie（`ADMIN_SECRET_KEY`，回退旧名 `FLASK_SECRET_KEY`）+ CSRF 头校验；缺配置拒绝启动。
- `admin/tasks.py` — 网页端生成任务，**镜像 `services/queue.py` 的 ComfyUI 流程**（翻译/脸部提取门控/上传/心跳/落盘），免额度；改 queue.py 流程时需同步此处。
- `admin/store.py` — 工作流配置 CRUD（原子写；内容未变不写盘；保留 CRLF/末尾换行）+ 网页端历史（`data/web_generations/`，上限 200 条）。
- `admin/validators.py` — 配置/节点校验，与 Bot 端 `comfy_api.validate_workflow()` 强制项对齐。
- `admin/static/` — 无构建 SPA（index.html/app.js/style.css），不依赖 CDN。
- 依赖在 pyproject 的 `admin` extra（fastapi/uvicorn/python-multipart），Docker 用 `Dockerfile.admin`。

## 外部服务

| 服务 | 地址 | 超时 |
|------|------|------|
| ComfyUI | `COMFY_API_BASE`（默认 `10.126.126.4:8188`） | 1500s |
| SD WebUI | `SD_API_BASE`（默认 `10.126.126.1:7860`） | 180s |
| DeepSeek 翻译 | `DEEPSEEK_BASE_URL` | 默认 |

- 翻译失败时静默降级为原文，不阻断生成。
- 生成队列为全局串行，新任务自动排队。

## Docker 启动

多平台 Compose 覆盖文件：

```bash
./start.sh          # 启动/重建容器（Linux）
./stop.sh           # 停止容器（Linux）
./rebuild.sh        # 停止 + 重建启动（Linux）
start.bat           # Windows 双击（对应 .bat 版本）
```

结构说明：
- `docker-compose.yml` — 通用配置（build、volumes、env_file），不含平台相关项
- `docker-compose.linux.yml` — `network_mode: host`
- `docker-compose.windows.yml` — `extra_hosts` + 覆盖 `PROXY_URL`/`COMFY_API_BASE` 为 `host.docker.internal`

注意事项：
- `COPY . .` 有 layer cache，新增文件未生效时需 `--no-cache` rebuild。
- `.env` 不进入镜像，通过 `env_file` 注入，Linux/Windows 共享同一个 `.env`。
- data/ 和 logs/ 通过 volume 挂载持久化。
- `network_mode: host` Windows Docker Desktop 不支持，因此必须使用覆盖文件。
- Windows 上 ComfyUI 需监听 `0.0.0.0:8188`（`--listen 0.0.0.0`）或已有端口映射，否则容器无法通过 `host.docker.internal` 访问。

## 代理

中国大陆网络环境必须配置 `PROXY_URL`（socks5/http），否则无法连接 Telegram API。
- Linux：`socks5://10.126.126.1:10808`
- Windows：`socks5://host.docker.internal:10808`

## 修改代码时

- `.env` 只放敏感信息（token、key、地址），常量放在 `config.py`。
- 新增工作流：在 `config.py` 的 `WORKFLOW_REGISTRY` 和 `COMFY_WORKFLOWS` 中注册，workflow JSON 放 `data/` 目录。
- 新增 handler 文件后，在 `bot.py` 中 import 并 `add_handlers()`，注意注册顺序。
- 配置变更不需要重启即可生效（`load_dotenv()` 在 `config.py` import 时执行，但环境变量需重启容器才能更新）。
