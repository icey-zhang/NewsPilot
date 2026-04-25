# coding=utf-8
"""
环境变量读取工具。
"""

from __future__ import annotations

import os
from typing import Optional

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


def get_env(*keys: str, default: Optional[str] = "") -> Optional[str]:
    for key in keys:
        value = os.environ.get(key)
        if value is None:
            continue
        value = value.strip()
        if not value:
            continue
        return value
    return default


def env_flag(*keys: str, default: bool = False) -> bool:
    value = get_env(*keys, default=None)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in _TRUTHY:
        return True
    if normalized in _FALSY:
        return False
    return default


def env_int(*keys: str, default: int) -> int:
    value = get_env(*keys, default=None)
    if value is None:
        return default
    try:
        return int(value)
    except Exception:
        return default
