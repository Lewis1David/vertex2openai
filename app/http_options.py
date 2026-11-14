from typing import Optional
import ssl
import httpx
from google.genai import types
import config as app_config
from proxy_manager import proxy_pool

try:
    import aiohttp  # noqa: F401
    _HAS_AIOHTTP = True
except ImportError:
    _HAS_AIOHTTP = False

# Google GenAI SDK 的 HttpOptions.timeout 单位为毫秒（600_000 ms = 10 分钟），
# 防止 gemini-4-argon 等长思考模型在假流式或深思考期间被默认 60s 超时掐断。
DEFAULT_TIMEOUT_MS = 600_000
DEFAULT_HTTPX_TIMEOUT = httpx.Timeout(connect=30.0, read=600.0, write=30.0, pool=10.0)


def get_http_options(
    base_url: Optional[str] = None,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    proxy_url: Optional[str] = None,
) -> types.HttpOptions:
    """构造 google-genai HTTP 选项：长思考超时、代理、自定义证书、base_url。

    代理每次调用都向代理池现取，因此控制台切换代理后无需重启即可生效；
    pool 模式下还会按策略轮换出口 IP。
    """
    # 传了 proxy_url 就用它（按 Key 固定出口），否则向代理池现取
    proxy_url = proxy_url or proxy_pool.pick()

    client_args = {"timeout": DEFAULT_HTTPX_TIMEOUT}
    if proxy_url:
        client_args["proxy"] = proxy_url
    if app_config.SSL_CERT_FILE:
        client_args["verify"] = app_config.SSL_CERT_FILE

    if _HAS_AIOHTTP:
        # aiohttp 模式下 async_client_args 会直接解包传给 ClientSession.request()，
        # 超时已由顶层 HttpOptions.timeout 控制，不能重复传 timeout。
        async_client_args = {}
        if proxy_url:
            async_client_args["proxy"] = proxy_url
        if app_config.SSL_CERT_FILE:
            async_client_args["ssl"] = ssl.create_default_context(cafile=app_config.SSL_CERT_FILE)
    else:
        async_client_args = dict(client_args)

    options = {
        "timeout": timeout_ms,
        "client_args": client_args,
        "async_client_args": async_client_args,
    }
    resolved_base_url = base_url or (app_config.VERTEX_BASE_URL or None)
    if resolved_base_url:
        options["base_url"] = resolved_base_url

    return types.HttpOptions(**options)
