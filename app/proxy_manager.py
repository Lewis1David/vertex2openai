"""出站代理池（控制台可热切换，无需重启进程）。

设计要点
--------
1. 四档模式：
     env    —— 跟随环境变量 ``PROXY_URL``（旧行为，默认，兼容已有部署）
     off    —— 强制直连（即使环境变量配了代理也忽略，用于排障）
     single —— 固定使用列表里的第一个代理
     pool   —— 多代理轮换，支持 round_robin / random，失败自动冷却隔离
2. 轮换粒度是「每次构造上游 HTTP 客户端」，也就是「单次上游调用 / 单次重试」，
   不是整个进程固定一个出口 IP。
3. 运行态在内存，配置通过 ``app_state`` 的 settings 持久化到 ``web_state.json``，
   容器重建后仍然生效；``PROXY_URL`` 环境变量始终作为 env 模式的来源保留。

所有日志与接口返回都经过 ``mask_proxy`` 脱敏，避免代理账号密码泄漏到前端/日志。
"""

from __future__ import annotations

import random
import re
import threading
import time
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlsplit, urlunsplit

import config as app_config

SUPPORTED_SCHEMES = ("http", "https", "socks5", "socks5h", "socks4")
DEFAULT_PORTS = {"http": 80, "https": 443, "socks4": 1080, "socks5": 1080, "socks5h": 1080}

MODES = ("env", "off", "single", "pool")
STRATEGIES = ("round_robin", "random")

BASE_COOLDOWN_SECONDS = 60.0
MAX_COOLDOWN_SECONDS = 600.0

_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------

def normalize_proxy(raw: str) -> str:
    """把用户输入规范化为 httpx 可直接使用的代理 URL。

    支持 ``host:port``（自动补 http://）以及带账号密码的写法：
    ``http://user:pass@1.2.3.4:8080``、``socks5://user:pass@1.2.3.4:1080``。
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "http://" + text

    parts = urlsplit(text)
    scheme = (parts.scheme or "").lower()
    if scheme not in SUPPORTED_SCHEMES:
        raise ValueError(
            f"不支持的代理协议：{scheme or '(空)'}；可用 http / https / socks5 / socks5h / socks4"
        )
    if not parts.hostname:
        raise ValueError(f"代理地址缺少主机名：{raw}")

    userinfo = ""
    if parts.username is not None:
        userinfo = parts.username
        if parts.password is not None:
            userinfo += ":" + parts.password
        userinfo += "@"

    host = parts.hostname
    if ":" not in host and not _HOSTNAME_RE.match(host):
        raise ValueError(f"代理主机名非法：{host}")
    if ":" in host:  # IPv6 字面量需要方括号
        host = f"[{host}]"
    port = parts.port if parts.port is not None else DEFAULT_PORTS[scheme]
    netloc = f"{userinfo}{host}:{port}"

    return urlunsplit((scheme, netloc, parts.path or "", parts.query or "", ""))


def mask_proxy(url: str) -> str:
    """脱敏：把密码替换为 ***，其余保持原样，方便用户确认是哪条线路。"""
    text = str(url or "").strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return text
    if parts.password is None:
        return text

    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    port = f":{parts.port}" if parts.port else ""
    netloc = f"{parts.username}:***@{host}{port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))


def _dedupe(items: Iterable[str]) -> List[str]:
    seen, out = set(), []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


# --------------------------------------------------------------------------
# 代理池
# --------------------------------------------------------------------------

class ProxyPool:
    """线程安全的代理池 + 健康度记录。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._mode = "env"
        self._strategy = "round_robin"
        self._items: List[str] = []
        self._cursor = 0
        self._health: Dict[str, Dict[str, Any]] = {}

    # ---------- 配置 ----------

    @property
    def mode(self) -> str:
        with self._lock:
            return self._mode

    @property
    def strategy(self) -> str:
        with self._lock:
            return self._strategy

    @property
    def items(self) -> List[str]:
        with self._lock:
            return list(self._items)

    def apply(self, mode: str, proxies: Iterable[str], strategy: str = "round_robin") -> Dict[str, Any]:
        """控制台保存时调用：校验 → 生效 → 返回脱敏快照。"""
        mode = str(mode or "env").strip().lower()
        strategy = str(strategy or "round_robin").strip().lower()
        if mode not in MODES:
            raise ValueError(f"未知的代理模式：{mode}")
        if strategy not in STRATEGIES:
            raise ValueError(f"未知的轮换策略：{strategy}")

        normalized: List[str] = []
        for idx, raw in enumerate(list(proxies or []), start=1):
            text = str(raw or "").strip()
            if not text or text.startswith("#"):
                continue
            try:
                normalized.append(normalize_proxy(text))
            except ValueError as exc:
                raise ValueError(f"第 {idx} 条代理无效：{exc}") from None

        normalized = _dedupe(normalized)
        if mode in ("single", "pool") and not normalized:
            raise ValueError("该模式至少需要一条代理地址。")

        with self._lock:
            self._mode = mode
            self._strategy = strategy
            self._items = normalized
            self._cursor = 0
            return self._snapshot_locked()

    def load_from_settings(self, settings: Dict[str, Any]) -> None:
        """启动时从 web_state.json 恢复（忽略非法值，保证服务能起来）。"""
        mode = str(settings.get("proxy_mode", "env") or "env").strip().lower()
        strategy = str(settings.get("proxy_strategy", "round_robin") or "round_robin").strip().lower()
        raw_items = settings.get("proxy_pool") or []
        if isinstance(raw_items, str):
            raw_items = [raw_items]

        items: List[str] = []
        for raw in raw_items:
            try:
                items.append(normalize_proxy(str(raw)))
            except ValueError:
                continue

        with self._lock:
            self._mode = mode if mode in MODES else "env"
            self._strategy = strategy if strategy in STRATEGIES else "round_robin"
            self._items = _dedupe(items)

    # ---------- 取用与回报 ----------

    def env_proxy(self) -> Optional[str]:
        """环境变量代理（兼容旧部署；空串视为未配置）。"""
        raw = str(getattr(app_config, "PROXY_URL", "") or "").strip()
        return raw or None

    def pick(self, exclude: Optional[Iterable[str]] = None) -> Optional[str]:
        """为「这一次上游客户端构造」挑一个代理；返回 None 表示直连。"""
        excluded = set(exclude or ())
        with self._lock:
            mode = self._mode
            if mode == "off":
                return None
            if mode == "env":
                env = self.env_proxy()
                return env if env not in excluded else None
            if not self._items:
                return None
            if mode == "single":
                head = self._items[0]
                return head if head not in excluded else None

            now = time.time()
            total = len(self._items)
            healthy: List[str] = []
            for offset in range(total):
                item = self._items[(self._cursor + offset) % total]
                if item in excluded:
                    continue
                state = self._health.get(item) or {}
                if float(state.get("cooldown_until", 0.0) or 0.0) <= now:
                    healthy.append(item)
            pool = healthy or [i for i in self._items if i not in excluded]
            if not pool:
                return None
            if self._strategy == "random":
                return random.choice(pool)
            chosen = pool[0]
            self._cursor = (self._items.index(chosen) + 1) % total
            return chosen

    def report(self, proxy: Optional[str], ok: bool, error: str = "") -> None:
        """回报一次调用结果：失败累积退避，成功清零。"""
        proxy = str(proxy or "").strip()
        if not proxy:
            return
        with self._lock:
            state = self._health.setdefault(proxy, {"fails": 0, "cooldown_until": 0.0,
                                                    "last_ok": 0.0, "last_error": ""})
            now = time.time()
            if ok:
                state["fails"] = 0
                state["cooldown_until"] = 0.0
                state["last_ok"] = now
                state["last_error"] = ""
                return
            fails = int(state.get("fails", 0)) + 1
            state["fails"] = fails
            cooldown = min(BASE_COOLDOWN_SECONDS * (2 ** (fails - 1)), MAX_COOLDOWN_SECONDS)
            state["cooldown_until"] = now + cooldown
            state["last_error"] = str(error or "")[:200]
            if len(self._items) > 1:
                print(f"⚠️ [代理池] {mask_proxy(proxy)} 调用失败（第 {fails} 次），"
                      f"冷却 {int(cooldown)} 秒后重新参与轮换：{state['last_error']}")

    def note_success(self, proxy: Optional[str]) -> None:
        self.report(proxy, True)

    # ---------- 状态展示 ----------

    def _peek_locked(self) -> str:
        """不推进游标的「当前出口」预览。"""
        if self._mode == "off":
            return ""
        if self._mode == "env":
            return self.env_proxy() or ""
        if not self._items:
            return ""
        if self._mode == "single":
            return self._items[0]
        now = time.time()
        for offset in range(len(self._items)):
            item = self._items[(self._cursor + offset) % len(self._items)]
            if float((self._health.get(item) or {}).get("cooldown_until", 0.0) or 0.0) <= now:
                return item
        return self._items[0]

    def _snapshot_locked(self, reveal: bool = False) -> Dict[str, Any]:
        now = time.time()
        health = []
        for item in self._items:
            state = self._health.get(item) or {}
            cooldown_left = max(0, int(float(state.get("cooldown_until", 0.0) or 0.0) - now))
            health.append({
                "proxy": mask_proxy(item),
                "fails": int(state.get("fails", 0) or 0),
                "cooldown_left": cooldown_left,
                "last_error": str(state.get("last_error", "") or ""),
            })
        env = self.env_proxy() or ""
        return {
            "mode": self._mode,
            "strategy": self._strategy,
            "proxies": [i if reveal else mask_proxy(i) for i in self._items],
            "count": len(self._items),
            "env_configured": bool(env),
            "env_proxy": mask_proxy(env),
            "active": mask_proxy(self._peek_locked()),
            "health": health,
        }

    def snapshot(self, reveal: bool = False) -> Dict[str, Any]:
        """reveal=True 时返回完整代理串（控制台已登录，项目本身也是明文展示 Key/Cookie）。"""
        with self._lock:
            return self._snapshot_locked(reveal)

    def describe(self) -> str:
        snap = self.snapshot()
        mode = snap["mode"]
        if mode == "off":
            return "直连（已禁用代理）"
        if mode == "env":
            return f"环境变量 PROXY_URL（{snap['env_proxy'] or '未配置'}）"
        return f"{mode}/{snap['strategy']}，共 {snap['count']} 条，当前 {snap['active'] or '-'}"

    # ---------- 连通性自检 ----------

    async def test_one(self, proxy: str, timeout: float = 12.0) -> Dict[str, Any]:
        """单条线路自检：先试出口 IP 查询，失败再退到 Google 的 204 探针。

        ipify 在部分网络下不可达，而 gstatic 才是真正和上游同源的连通性指标，
        所以第二个目标只判「能不能通」，不要求拿到 IP。
        """
        import httpx

        started = time.time()
        kwargs: Dict[str, Any] = {
            "timeout": httpx.Timeout(connect=min(timeout, 8.0), read=timeout, write=timeout, pool=timeout),
            "follow_redirects": True,
        }
        if proxy:
            kwargs["proxy"] = proxy
        if getattr(app_config, "SSL_CERT_FILE", None):
            kwargs["verify"] = app_config.SSL_CERT_FILE

        last_error = ""
        try:
            async with httpx.AsyncClient(**kwargs) as client:
                for url, want_ip in (
                    ("https://api.ipify.org?format=json", True),
                    ("https://www.gstatic.com/generate_204", False),
                ):
                    try:
                        resp = await client.get(url, timeout=5.0 if want_ip else timeout)
                        resp.raise_for_status()
                        ip = ""
                        if want_ip:
                            try:
                                ip = str(resp.json().get("ip", "") or "")
                            except Exception:
                                ip = resp.text.strip()[:64]
                        return {
                            "proxy": mask_proxy(proxy),
                            "ok": True,
                            "ip": ip,
                            "via": url,
                            "latency_ms": int((time.time() - started) * 1000),
                            "error": "",
                        }
                    except Exception as exc:
                        last_error = f"{type(exc).__name__}: {exc}"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"

        return {
            "proxy": mask_proxy(proxy),
            "ok": False,
            "ip": "",
            "via": "",
            "latency_ms": int((time.time() - started) * 1000),
            "error": last_error,
        }

    async def test_many(self, proxies: Optional[List[str]] = None, concurrency: int = 6) -> List[Dict[str, Any]]:
        import asyncio

        with self._lock:
            if proxies:
                targets = list(proxies)
            elif self._mode == "off":
                targets = [""]                      # 强制直连：测直连
            elif self._mode == "env":
                env = self.env_proxy()
                targets = [env] if env else [""]
            else:
                targets = list(self._items) or [""]  # single / pool：测列表里的线路
            if not targets:
                targets = [""]

        semaphore = asyncio.Semaphore(max(1, concurrency))

        async def _run(item: str) -> Dict[str, Any]:
            async with semaphore:
                result = await self.test_one(item)
                if item:
                    self.report(item, result["ok"], result["error"])
                return result

        return list(await asyncio.gather(*(_run(t) for t in targets)))


# 单例
proxy_pool = ProxyPool()
