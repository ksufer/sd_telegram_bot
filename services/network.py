import asyncio
import logging
import ssl
from typing import Any, Awaitable, Callable, Optional

import httpx
import telegram.error
from openai import APIConnectionError, APITimeoutError

logger = logging.getLogger(__name__)


def is_network_error(exc: Exception) -> bool:
    """判断是否为可重试的瞬时网络错误，排除 BadRequest 等永久性错误。"""
    if isinstance(exc, telegram.error.TimedOut):
        return True
    if isinstance(exc, telegram.error.NetworkError) and not isinstance(
        exc, telegram.error.BadRequest
    ):
        return True
    if isinstance(exc, (
        httpx.ConnectError,
        httpx.TimeoutException,
        httpx.RemoteProtocolError,
        httpx.ReadError,
        httpx.WriteError,
    )):
        return True
    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        return True
    if isinstance(exc, ssl.SSLError):
        return True
    return False


async def retry_on_network_error(
    coro_factory: Callable[[], Awaitable[Any]],
    max_retries: int = 3,
    base_delay: float = 1.0,
    on_retry: Optional[Callable[[int, int], Awaitable[None]]] = None,
) -> Any:
    """带指数退避的网络重试包装器。

    coro_factory: 每次重试时调用的工厂函数，返回一个 awaitable
    on_retry: 可选回调，签名为 async def(attempt, max_retries)，用于通知用户
    """
    last_exc: Exception
    for attempt in range(1, max_retries + 1):
        try:
            return await coro_factory()
        except Exception as e:
            last_exc = e
            if not is_network_error(e):
                raise
            if attempt < max_retries:
                delay = base_delay * (2 ** (attempt - 1))
                logger.warning("网络请求失败 (attempt %s/%s): %s", attempt, max_retries, e)
                if on_retry:
                    try:
                        await on_retry(attempt, max_retries)
                    except Exception:
                        pass
                await asyncio.sleep(delay)
    raise last_exc
