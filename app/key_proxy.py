"""按 Express Key 绑定固定出口代理（可选功能）。

通过环境变量 ``EXPRESS_KEY_PROXIES`` 配置，逗号分隔，顺序与控制台 Key 列表一致：

    EXPRESS_KEY_PROXIES=http://mihomo:7901,http://mihomo:7904

第 1 条 Key 走 7901，第 2 条走 7904；没有配置到的 Key（或配置为空项）回落到
``proxy_pool.pick()``，也就是控制台「出站代理」里的设置。
"""

from typing import Optional

import config as app_config


def proxy_for_index(index: int) -> Optional[str]:
    """按 Key 在控制台列表中的下标取绑定代理；没有配置则返回 None。"""
    entries = getattr(app_config, "EXPRESS_KEY_PROXIES_VAL", None) or []
    try:
        idx = int(index)
    except (TypeError, ValueError):
        return None
    if idx < 0 or idx >= len(entries):
        return None
    return entries[idx] or None


def proxy_for_record(record: dict) -> Optional[str]:
    """按 Key 记录（带 index 字段）取绑定代理。"""
    if not isinstance(record, dict):
        return None
    return proxy_for_index(record.get("index", -1))
