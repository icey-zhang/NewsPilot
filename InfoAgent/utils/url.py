# coding=utf-8
"""
URL 处理工具模块

提供 URL 标准化功能：
- normalize_url: 通用标准化（主要用于热榜 URL 去重）
- normalize_rss_url_key: RSS/文章链接的“匹配键”标准化（用于按 URL 关联 LLM 富化结果）
"""

from urllib.parse import urlparse, urlunparse, parse_qs, urlencode
from typing import Dict, Set


# 各平台需要移除的特定参数
#   - weibo: 有 band_rank（排名）和 Refer（来源）动态参数
#   - 其他平台: URL 为路径格式或简单关键词查询，无需处理
PLATFORM_PARAMS_TO_REMOVE: Dict[str, Set[str]] = {
    # 微博：band_rank 是动态排名参数，Refer 是来源参数，t 是时间范围参数
    # 示例：https://s.weibo.com/weibo?q=xxx&t=31&band_rank=1&Refer=top
    # 保留：q（关键词）
    # 移除：band_rank, Refer, t
    "weibo": {"band_rank", "Refer", "t"},
}

# 通用追踪参数（适用于所有平台）
# 这些参数通常由分享链接或广告追踪添加，不影响内容识别
COMMON_TRACKING_PARAMS: Set[str] = {
    # UTM 追踪参数
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    # 常见追踪参数
    "ref", "referrer", "source", "channel",
    # 时间戳和随机参数
    "_t", "timestamp", "_", "random",
    # 分享相关
    "share_token", "share_id", "share_from",
}

RSS_TRACKING_PARAMS: Set[str] = {
    # WeChat/公众号等常见“展示/来源”参数（不影响文章唯一性）
    "scene",
    "subscene",
    "xtrack",
    "from",
    "src",
    "source",
    "share_source",
}


def normalize_url(url: str, platform_id: str = "") -> str:
    """
    标准化 URL，去除动态参数

    用于数据库去重，确保同一条新闻的不同 URL 变体能被正确识别为同一条。

    处理规则：
    1. 去除平台特定的动态参数（如微博的 band_rank）
    2. 去除通用追踪参数（如 utm_*）
    3. 保留核心查询参数（如搜索关键词 q=, wd=, keyword=）
    4. 对查询参数按字母序排序（确保一致性）

    Args:
        url: 原始 URL
        platform_id: 平台 ID，用于应用平台特定规则

    Returns:
        标准化后的 URL

    Examples:
        >>> normalize_url("https://s.weibo.com/weibo?q=test&band_rank=6&Refer=top", "weibo")
        'https://s.weibo.com/weibo?q=test'

        >>> normalize_url("https://example.com/page?id=1&utm_source=twitter", "")
        'https://example.com/page?id=1'
    """
    if not url:
        return url

    try:
        # 解析 URL
        parsed = urlparse(url)

        # 如果没有查询参数，直接返回
        if not parsed.query:
            return url

        # 解析查询参数
        params = parse_qs(parsed.query, keep_blank_values=True)

        # 收集需要移除的参数（使用小写进行比较）
        params_to_remove: Set[str] = set()

        # 添加通用追踪参数
        params_to_remove.update(COMMON_TRACKING_PARAMS)

        # 添加平台特定参数
        if platform_id and platform_id in PLATFORM_PARAMS_TO_REMOVE:
            params_to_remove.update(PLATFORM_PARAMS_TO_REMOVE[platform_id])

        # 过滤参数（参数名转小写进行比较）
        filtered_params = {
            key: values
            for key, values in params.items()
            if key.lower() not in {p.lower() for p in params_to_remove}
        }

        # 如果过滤后没有参数了，返回不带查询字符串的 URL
        if not filtered_params:
            return urlunparse((
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                "",  # 空查询字符串
                ""   # 移除 fragment
            ))

        # 重建查询字符串（按字母序排序以确保一致性）
        sorted_params = []
        for key in sorted(filtered_params.keys()):
            for value in filtered_params[key]:
                sorted_params.append((key, value))

        new_query = urlencode(sorted_params)

        # 重建 URL（移除 fragment）
        normalized = urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            ""  # 移除 fragment
        ))

        return normalized

    except Exception:
        # 解析失败时返回原始 URL
        return url


def normalize_rss_url_key(url: str) -> str:
    """
    标准化 RSS 条目 URL 的“匹配键”（用于把 LLM 富化结果挂回 HTML 渲染的 RSS 条目上）。

    目标是提升“同一文章不同 URL 变体”的命中率（如 `#rd`、参数顺序不同、带/不带展示参数等）。
    注意：这是“匹配键”，不会影响最终点击跳转用的原始 URL。
    """
    if not url:
        return ""

    url = str(url).strip()
    if not url:
        return ""

    try:
        parsed = urlparse(url)
        scheme = (parsed.scheme or "").lower()
        netloc = (parsed.netloc or "").lower()
        path = parsed.path or ""

        # For matching only: normalize http -> https to reduce mismatches
        if scheme in {"http", "https"}:
            scheme = "https"

        # Always drop fragment (e.g. #rd)
        fragment = ""

        query = parsed.query or ""
        if query:
            params = parse_qs(query, keep_blank_values=True)
            params_to_remove = {p.lower() for p in COMMON_TRACKING_PARAMS | RSS_TRACKING_PARAMS}
            filtered_params = {
                k: v for k, v in params.items() if (k or "").lower() not in params_to_remove
            }
            # sort for stable key
            sorted_params = []
            for k in sorted(filtered_params.keys()):
                for v in filtered_params[k]:
                    sorted_params.append((k, v))
            query = urlencode(sorted_params)

        return urlunparse((scheme, netloc, path, parsed.params, query, fragment))
    except Exception:
        # fallback: at least strip common fragment form
        return url.split("#", 1)[0].strip()


def get_url_signature(url: str, platform_id: str = "") -> str:
    """
    获取 URL 的签名（用于快速比较）

    基于标准化 URL 生成签名，可用于：
    - 快速判断两个 URL 是否指向同一内容
    - 作为缓存键

    Args:
        url: 原始 URL
        platform_id: 平台 ID

    Returns:
        URL 签名字符串
    """
    return normalize_url(url, platform_id)
