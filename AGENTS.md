# AGENTS.md

为 OpenCode 等 AI 编码助手提供的高信号操作指引。详细架构和部署说明见 `CLAUDE.md`。

## 命令

```bash
uv run python bot.py    # 启动 Bot（唯一入口，不要用 main.py）
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
- 权限控制：`handlers/__init__.py` 提供 `is_authorized()`、`auth_callback` 装饰器、`_user_auth_filter()`。管理员无需在白名单中。

## 外部服务

| 服务 | 地址 | 超时 |
|------|------|------|
| ComfyUI | `COMFY_API_BASE`（默认 `10.126.126.4:8188`） | 1500s |
| SD WebUI | `SD_API_BASE`（默认 `10.126.126.1:7860`） | 180s |
| DeepSeek 翻译 | `DEEPSEEK_BASE_URL` | 默认 |

- 翻译失败时静默降级为原文，不阻断生成。
- 生成队列为全局串行，新任务自动排队。

## Docker 注意事项

- `network_mode: host` — 容器直接访问宿主机网络（ComfyUI、代理等）。
- `COPY . .` 有 layer cache，新增文件未生效时需 `--no-cache` rebuild。
- `.env` 不进入镜像，通过 `env_file` 注入。
- data/ 和 logs/ 通过 volume 挂载持久化。
- 推送命令：`rsync` 排除 `.git`、`.venv`、`__pycache__` 等，目标 `homelab:/home/ksufer/homelab/stacks/sd-telegram-bot/`。

## 代理

中国大陆网络环境必须配置 `PROXY_URL`（socks5/http），否则无法连接 Telegram API。容器内代理指向宿主机 `socks5://10.126.126.1:10808`。

## 修改代码时

- `.env` 只放敏感信息（token、key、地址），常量放在 `config.py`。
- 新增工作流：在 `config.py` 的 `WORKFLOW_REGISTRY` 和 `COMFY_WORKFLOWS` 中注册，workflow JSON 放 `data/` 目录。
- 新增 handler 文件后，在 `bot.py` 中 import 并 `add_handlers()`，注意注册顺序。
- 配置变更不需要重启即可生效（`load_dotenv()` 在 `config.py` import 时执行，但环境变量需重启容器才能更新）。
