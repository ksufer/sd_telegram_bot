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

## CodeGraph 代码索引

项目使用 [CodeGraph](https://www.npmjs.com/package/@colbymchenry/codegraph) 维护符号级代码索引（SQLite，存于 `.codegraph/`，已 gitignore）。CLI 为全局安装的 `codegraph`（npm 包 `@colbymchenry/codegraph`）。探索代码、查调用关系时优先用它，比全文 grep 更快更准。

```bash
codegraph status            # 查看索引状态/统计
codegraph sync              # 增量同步（改了代码后跑这个）
codegraph index             # 全量重建（引擎升级或索引异常时用）
codegraph query <符号>       # 搜索符号
codegraph explore <查询>     # 一次拿到相关符号源码 + 调用路径（探索陌生代码首选）
codegraph node <符号|文件>   # 单个符号源码 + caller/callee 轨迹，或按行读文件
codegraph callers <符号>     # 谁调用了它
codegraph callees <符号>     # 它调用了谁
codegraph impact <符号>      # 改动影响面分析
codegraph affected [文件]    # 改动文件影响了哪些测试
```

- MCP 客户端（Claude Code / opencode 等）接入时 daemon 会通过文件监听自动同步；纯 CLI 使用则改完代码手动 `codegraph sync`。
- `codegraph status` 若提示 "interrupted run" 或 "built by an earlier version"，跑 `codegraph sync` 或 `codegraph index` 修复。

## 架构要点

- `bot.py` 是唯一入口（`main.py` 是占位模板，忽略它）。
- `concurrent_updates(False)` — Bot 串行处理消息，无需担心并发。
- Handler 注册顺序决定匹配优先级（`bot.py:68-74`）：workflow_menu → gacha → pipeline → settings → generation → credits → comfy_settings。
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
| `handlers/gacha.py` | 灵感抽卡交互（`/gacha` 命令 + 主菜单「🎰 灵感抽卡」按钮；卡片重抽/单项重抽/SFW-NSFW 切换/直接生成） |
| `services/gacha.py` | 抽卡词库加载与抽词逻辑（`data/prompt_gacha.json`，按 mtime 缓存热生效；维度含 `skip_chance`/`nsfw_only`，词为 `{"en","zh"}`） |
| `handlers/pipeline.py` | Pipeline 动态编排（主菜单「⛓ Pipeline」；步骤列表持久化在 `settings["pipeline_steps"]`，编排/增删/排序/运行） |
| `handlers/rev_prompt.py` | 图片反推提示词交互（主菜单「🔍 反推提示词」；等待标记 `_waiting_input="rev_prompt"`，由 `handle_photo`/`handle_text` 顶部分发；扣 1 额度入队） |
| `services/ollama_api.py` | Ollama 视觉模型反推（单次 `/api/chat` 同时产出 SD 标签词 + Krea 2 句子版 JSON；解析失败修复重试一次；请求 keep_alive 5m、结束显式卸载归还显存） |

### 生成流程

- `handlers/generation.py` 中 `handle_text()` / `handle_photo()` 通过 5 个辅助函数（`_check_and_charge_credit`、`_create_status_message`、`_download_tg_photo`、`_upload_to_comfy`、`_enqueue_and_notify`）消除重复代码，退款统一在调用方处理。
- `services/queue.py` 中 `_process_task()` 已拆分为 `_translate_prompt`、`_generate`、`_send_result`、`_cache_gen_context` 私有方法。
- `services/comfy_api.py` 中 `_build_payload()` 已拆分为 8 个 `_apply_*` 函数。

### Pipeline 动态编排

- 用户在菜单里编排有序步骤（≥2 步），运行后每步是**独立任务**：独立扣 1 额度、独立状态消息，其他用户任务可穿插。
- 连跑由 `queue._maybe_chain_pipeline()` 完成：上一步发送成功后 `comfy_api.upload_image()` 回注输出图 → 组下一步任务入队；任何失败仅通知并中止，已交付步骤不受影响。
- 步骤持久化在 `settings["pipeline_steps"]`，元素为 `{"key", "prompt"}`（prompt 为预设提示词，空=运行时询问；旧格式纯字符串读取时自动迁移）。`GenerationTask.pipeline = {"steps", "idx", "prompts", "ref_images"}`（内存对象，int 键）。
- **每步提示词独立**：编辑步的提示词与文生图步不同；运行时按需收集缺失的 prompt（无预设时回退上一步 prompt）。
- **双图编辑步**（如 qwen-2pic-edit / f2k-2pic-edit 换装）：产出图注入第 1 个角色（主图），运行时向用户收集的参考图注入第 2 个角色（`ref_images`）；运行时收集计划存于 `user_data["_pipe_collect"]`（items/pos/prompts/images）。
- **重复执行**：运行时输入的 prompt 自动固化为步骤预设；图片文件名缓存在 `user_data["_pipe_last_images"]`（会话级）。再次运行时输入齐备 → 弹「运行确认」页（`pipe:go` 直接开始 / `pipe:reimg` 清缓存重选图片），缺啥问啥。
- 步骤合法性：仅图片输出的 ComfyUI 工作流；首步为文生图或单图图生图；后续步为单图或双角色图生图；视频工作流不可作为步骤。
- 每步使用自己工作流的 `default_model` 解析链（覆盖/弹出 `comfy_model`），不用用户全局模型。
- seed 每步独立（各自的 `_gen_context` / reuse 按钮照常）。
- 会话标记：`_waiting_input` 取值 `"pipe_collect"`（收集提示词）/ `"pipe_step_prompt"`（编辑预设）；`_pipe_collect` / `_pipe_edit_step`；由 `handle_text`/`handle_photo` 顶部分发（延迟 import 避免循环），`/cancel` 统一清理。
- **admin/tasks.py 不镜像** pipeline 连跑（网页端不创建带 `pipeline` 字段的任务，走原单步路径）。

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
| Ollama 反推 | `OLLAMA_BASE_URL`（默认 `10.126.126.4:11434`，模型 `OLLAMA_MODEL`） | `OLLAMA_TIMEOUT`（默认 900s） |

- 翻译失败时静默降级为原文，不阻断生成。
- 生成队列为全局串行，新任务自动排队。
- Ollama 反推与 ComfyUI 共享 GPU（16G 显存）：反推任务走同一串行队列与生成互斥，执行前 `comfy_api.free_memory()` 卸载 ComfyUI 模型，结束后显式卸载 ollama 模型归还显存。

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
- 新增工作流：在 `data/workflows/` 新建 schema_version=1 的注册配置（key 与文件名一致），并同步维护 `config.py` 中的回退默认值（`scripts/export_workflows.py` 的导出源）；ComfyUI workflow JSON 放入 `data/comfy_workflows/`。`data/workflows/` 与 `data/comfy_workflows/` 的改动热重载生效；`config.py` 默认值改动需重启进程。
- 新增 handler 文件后，在 `bot.py` 中 import 并 `add_handlers()`，注意注册顺序。
- 模型更新无需改配置：生成时 `comfy_api.resolve_model()` 对照 ComfyUI 实时模型列表解析，链为「用户 comfy_model → `default_model` → 家族最新（自然排序；可选 `default_model_pattern` glob 覆盖，默认从 `default_model` 剥离尾部版本号推导前缀）→ 列表第一个」。模型列表有 60s TTL 缓存（失败 15s）。
- 配置变更不需要重启即可生效（`load_dotenv()` 在 `config.py` import 时执行，但环境变量需重启容器才能更新）。
