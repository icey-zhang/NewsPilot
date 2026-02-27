# coding=utf-8
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple


RSS_ITEM_ENRICH_PROMPT_VERSION = 1


def build_rss_items_enrich_prompt(
    *,
    items: List[Dict[str, Any]],
    include_content: bool = False,
    language: str = "zh",
) -> Tuple[str, str, Dict[str, Any]]:
    """
    Build a prompt for per-item enrichment: title -> summary + viewpoint.

    Returns:
        (system, user, json_schema_hint)
    """
    if language != "zh":
        raise ValueError("Only zh is supported for now")

    system = (
        "你是一名长期关注 AI 技术与产业趋势的研究者与写作者，"
        "擅长从长文中抽取结构性信息，并用高度凝练、判断明确的语言表达核心价值。"
        "你必须严格输出 JSON（不要 Markdown，不要解释，不要多余文本）。"
    )

    user_lines = [
        "📌 任务：AI 文章「标题 + 总结 + 观点」压缩器",
        "",
        "我将提供一篇篇幅较长的 AI 相关文章（技术 / 产品 / 创业 / 产业分析）。",
        "请你将其压缩重写为以下固定结构（写作要求严格执行）：",
        "",
        "写作要求：",
        "- 总字数尽量短，能删就删",
        "- 不复述细节、不堆例子、不讲过程",
        "- 观点必须是抽象一层后的判断，而不是文章原话改写",
        "- 避免“很重要 / 具有里程碑意义 / 未来可期”等空话",
        "- 语气偏冷静、专业、研究者/产品分析视角",
        "",
        "判断侧重点（按优先级）：",
        "1) 是否改变了 AI 的交互方式 / 执行方式 / 数据获取方式",
        "2) 是否涉及系统级能力（Agent、端云协同、数据飞轮、硬件形态）",
        "3) 是否揭示下一阶段 AI 产品或产业的分水岭",
        "",
        "不要输出：背景科普/作者信息/投融资八卦/无关修辞",
        "",
        "输出字段要求（对应原“标题/总结/观点”三段）：",
        "- title：一句话，直接点出这篇文章真正讲的是什么，避免标题党",
        "- summary：2–3 句话，回答：发生了什么？解决了什么问题？核心做法是什么？不展开细节",
        "- viewpoint：2–3 句话，表达判断与趋势（可含不确定性声明）",
        "",
        "输入注意：如果提供了 content（正文），优先依据正文；否则依据标题/来源/发布时间等元信息。",
        "必须保留原始 url 用于匹配。",
        "",
        "返回 JSON 结构：",
        "{",
        '  "items": [',
        '    {"url": "...", "title": "...", "summary": "...", "viewpoint": "..."}',
        "  ]",
        "}",
        "",
        "RSS 条目：",
    ]

    for it in items:
        title = (it.get("title") or "").strip()
        url = (it.get("url") or "").strip()
        source = (it.get("feed_name") or it.get("feed_id") or "").strip()
        published = (it.get("published_at") or "").strip()
        content = (it.get("content") or "").strip() if include_content else ""
        if content:
            content = _normalize_content_for_prompt(content)
        user_lines.append(f'- title="{title}"')
        user_lines.append(f'  url="{url}"')
        if source:
            user_lines.append(f'  source="{source}"')
        if published:
            user_lines.append(f'  published_at="{published}"')
        if content:
            user_lines.append("  content=\"\"\"")
            user_lines.append(content)
            user_lines.append("\"\"\"")

    user = "\n".join(user_lines)

    json_schema_hint: Dict[str, Any] = {
        "items": [
            {
                "url": "string",
                "title": "string",
                "summary": "string (2-3 sentences, concise)",
                "viewpoint": "string (2-3 sentences, analytical)",
            }
        ]
    }

    return system, user, json_schema_hint


def build_rss_item_enrich_prompt(
    *,
    item: Dict[str, Any],
    include_content: bool = False,
    language: str = "zh",
) -> Tuple[str, str, Dict[str, Any]]:
    """
    Build a prompt for single-item enrichment to reduce prompt size and timeouts.

    Returns:
        (system, user, json_schema_hint)
    """
    if language != "zh":
        raise ValueError("Only zh is supported for now")

    system = (
        "你是一名长期关注 AI 技术与产业趋势的研究者与写作者，"
        "擅长从长文中抽取结构性信息，并用高度凝练、判断明确的语言表达核心价值。"
        "你必须严格输出 JSON（不要 Markdown，不要解释，不要多余文本）。"
    )

    title = (item.get("title") or "").strip()
    url = (item.get("url") or "").strip()
    source = (item.get("feed_name") or item.get("feed_id") or "").strip()
    published = (item.get("published_at") or "").strip()
    content = (item.get("content") or "").strip() if include_content else ""
    if content:
        content = _normalize_content_for_prompt(content)

    user_lines = [
        "📌 任务：AI 文章「标题 + 总结 + 观点」压缩器（单篇模式）",
        "",
        "写作要求：",
        "- 总字数尽量短，能删就删",
        "- 不复述细节、不堆例子、不讲过程",
        "- 观点必须是抽象一层后的判断，而不是文章原话改写",
        "- 避免“很重要 / 具有里程碑意义 / 未来可期”等空话",
        "- 语气偏冷静、专业、研究者/产品分析视角",
        "",
        "不要输出：背景科普/作者信息/投融资八卦/无关修辞",
        "",
        "输出字段要求：",
        "- title：一句话，直接点出这篇文章真正讲的是什么，避免标题党",
        "- summary：2–3 句话，回答：发生了什么？解决了什么问题？核心做法是什么？不展开细节",
        "- viewpoint：2–3 句话，表达判断与趋势（可含不确定性声明）",
        "",
        "输入注意：如果提供了 content（正文），优先依据正文；否则依据标题/来源/发布时间等元信息。",
        "必须保留原始 url 用于匹配。",
        "",
        "返回 JSON 结构：",
        '{"url":"...","title":"...","summary":"...","viewpoint":"..."}',
        "",
        "RSS 条目：",
        f'- title="{title}"',
        f'  url="{url}"',
    ]
    if source:
        user_lines.append(f'  source="{source}"')
    if published:
        user_lines.append(f'  published_at="{published}"')
    if content:
        user_lines.append('  content="""')
        user_lines.append(content)
        user_lines.append('"""')

    user = "\n".join(user_lines)
    json_schema_hint: Dict[str, Any] = {
        "url": "string",
        "title": "string",
        "summary": "string (2-3 sentences, concise)",
        "viewpoint": "string (2-3 sentences, analytical)",
    }
    return system, user, json_schema_hint


def _normalize_content_for_prompt(content: str) -> str:
    """
    Reduce noisy whitespace so the prompt focuses on readable paragraphs.
    """
    content = content.replace("\r", "\n")
    # trim each line
    content = "\n".join(line.strip() for line in content.split("\n"))
    # collapse excessive blank lines
    content = re.sub(r"\n{3,}", "\n\n", content).strip()
    return content
