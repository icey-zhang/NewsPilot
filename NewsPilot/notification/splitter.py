# coding=utf-8
"""
消息分批处理模块

提供消息内容分批拆分功能，确保消息大小不超过各平台限制
"""

from datetime import datetime
from typing import Dict, List, Optional, Callable

from NewsPilot.report.formatter import format_title_for_platform


# 默认批次大小配置
DEFAULT_BATCH_SIZES = {
    "dingtalk": 20000,
    "feishu": 29000,
    "ntfy": 3800,
    "default": 4000,
}


def split_content_into_batches(
    report_data: Dict,
    format_type: str,
    update_info: Optional[Dict] = None,
    max_bytes: Optional[int] = None,
    mode: str = "daily",
    batch_sizes: Optional[Dict[str, int]] = None,
    feishu_separator: str = "---",
    reverse_content_order: bool = False,
    get_time_func: Optional[Callable[[], datetime]] = None,
    rss_items: Optional[list] = None,
    rss_new_items: Optional[list] = None,
    timezone: str = "Asia/Shanghai",
    display_mode: str = "keyword",
) -> List[str]:
    """分批处理消息内容，确保词组标题+至少第一条新闻的完整性（支持热榜+RSS合并）

    热榜统计与RSS统计并列显示，热榜新增与RSS新增并列显示。
    reverse_content_order 控制统计和新增的前后顺序。

    Args:
        report_data: 报告数据字典，包含 stats, new_titles, failed_ids, total_new_count
        format_type: 格式类型 (feishu, dingtalk, wework, telegram, ntfy, bark, slack)
        update_info: 版本更新信息（可选）
        max_bytes: 最大字节数（可选，如果不指定则使用默认配置）
        mode: 报告模式 (daily, incremental, current)
        batch_sizes: 批次大小配置字典（可选）
        feishu_separator: 飞书消息分隔符
        reverse_content_order: 是否反转内容顺序（新增在前，统计在后）
        get_time_func: 获取当前时间的函数（可选）
        rss_items: RSS 统计条目列表（按源分组，用于合并推送）
        rss_new_items: RSS 新增条目列表（可选，用于新增区块）
        timezone: 时区名称（用于 RSS 时间格式化）
        display_mode: 显示模式 (keyword=按关键词分组, platform=按平台分组)

    Returns:
        分批后的消息内容列表
    """
    # 合并批次大小配置
    sizes = {**DEFAULT_BATCH_SIZES, **(batch_sizes or {})}

    if max_bytes is None:
        if format_type == "dingtalk":
            max_bytes = sizes.get("dingtalk", 20000)
        elif format_type == "feishu":
            max_bytes = sizes.get("feishu", 29000)
        elif format_type == "ntfy":
            max_bytes = sizes.get("ntfy", 3800)
        else:
            max_bytes = sizes.get("default", 4000)

    batches = []

    total_titles = sum(
        len(stat["titles"]) for stat in report_data["stats"] if stat["count"] > 0
    )
    now = get_time_func() if get_time_func else datetime.now()

    base_header = ""
    if format_type in ("wework", "bark"):
        base_header = f"**总新闻数：** {total_titles}\n\n\n\n"
    elif format_type == "telegram":
        base_header = f"总新闻数： {total_titles}\n\n"
    elif format_type == "ntfy":
        base_header = f"**总新闻数：** {total_titles}\n\n"
    elif format_type == "feishu":
        base_header = ""
    elif format_type == "dingtalk":
        base_header = f"**总新闻数：** {total_titles}\n\n"
        base_header += f"**时间：** {now.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        base_header += f"**类型：** 热点分析报告\n\n"
        base_header += "---\n\n"
    elif format_type == "slack":
        base_header = f"*总新闻数：* {total_titles}\n\n"

    base_footer = ""
    if format_type in ("wework", "bark"):
        base_footer = f"\n\n\n> 更新时间：{now.strftime('%Y-%m-%d %H:%M:%S')}"
        if update_info:
            base_footer += f"\n> NewsPilot 发现新版本 **{update_info['remote_version']}**，当前 **{update_info['current_version']}**"
    elif format_type == "telegram":
        base_footer = f"\n\n更新时间：{now.strftime('%Y-%m-%d %H:%M:%S')}"
        if update_info:
            base_footer += f"\nNewsPilot 发现新版本 {update_info['remote_version']}，当前 {update_info['current_version']}"
    elif format_type == "ntfy":
        base_footer = f"\n\n> 更新时间：{now.strftime('%Y-%m-%d %H:%M:%S')}"
        if update_info:
            base_footer += f"\n> NewsPilot 发现新版本 **{update_info['remote_version']}**，当前 **{update_info['current_version']}**"
    elif format_type == "feishu":
        base_footer = f"\n\n<font color='grey'>更新时间：{now.strftime('%Y-%m-%d %H:%M:%S')}</font>"
        if update_info:
            base_footer += f"\n<font color='grey'>NewsPilot 发现新版本 {update_info['remote_version']}，当前 {update_info['current_version']}</font>"
    elif format_type == "dingtalk":
        base_footer = f"\n\n> 更新时间：{now.strftime('%Y-%m-%d %H:%M:%S')}"
        if update_info:
            base_footer += f"\n> NewsPilot 发现新版本 **{update_info['remote_version']}**，当前 **{update_info['current_version']}**"
    elif format_type == "slack":
        base_footer = f"\n\n_更新时间：{now.strftime('%Y-%m-%d %H:%M:%S')}_"
        if update_info:
            base_footer += f"\n_NewsPilot 发现新版本 *{update_info['remote_version']}*，当前 *{update_info['current_version']}_"

    # 根据 display_mode 选择统计标题
    stats_title = "热点词汇统计" if display_mode == "keyword" else "热点新闻统计"
    stats_header = ""
    if report_data["stats"]:
        if format_type in ("wework", "bark"):
            stats_header = f"📊 **{stats_title}**\n\n"
        elif format_type == "telegram":
            stats_header = f"📊 {stats_title}\n\n"
        elif format_type == "ntfy":
            stats_header = f"📊 **{stats_title}**\n\n"
        elif format_type == "feishu":
            stats_header = f"📊 **{stats_title}**\n\n"
        elif format_type == "dingtalk":
            stats_header = f"📊 **{stats_title}**\n\n"
        elif format_type == "slack":
            stats_header = f"📊 *{stats_title}*\n\n"

    current_batch = base_header
    current_batch_has_content = False

    if (
        not report_data["stats"]
        and not report_data["new_titles"]
        and not report_data["failed_ids"]
    ):
        if mode == "incremental":
            mode_text = "增量模式下暂无新增匹配的热点词汇"
        elif mode == "current":
            mode_text = "当前榜单模式下暂无匹配的热点词汇"
        else:
            mode_text = "暂无匹配的热点词汇"
        simple_content = f"📭 {mode_text}\n\n"
        final_content = base_header + simple_content + base_footer
        batches.append(final_content)
        return batches

    # 定义处理热点词汇统计的函数
    def process_stats_section(current_batch, current_batch_has_content, batches):
        """处理热点词汇统计"""
        if not report_data["stats"]:
            return current_batch, current_batch_has_content, batches

        total_count = len(report_data["stats"])

        # 添加统计标题
        test_content = current_batch + stats_header
        if (
            len(test_content.encode("utf-8")) + len(base_footer.encode("utf-8"))
            < max_bytes
        ):
            current_batch = test_content
            current_batch_has_content = True
        else:
            if current_batch_has_content:
                batches.append(current_batch + base_footer)
            current_batch = base_header + stats_header
            current_batch_has_content = True

        # 逐个处理词组（确保词组标题+第一条新闻的原子性）
        for i, stat in enumerate(report_data["stats"]):
            word = stat["word"]
            count = stat["count"]
            sequence_display = f"[{i + 1}/{total_count}]"

            # 构建词组标题
            word_header = ""
            if format_type in ("wework", "bark"):
                if count >= 10:
                    word_header = (
                        f"🔥 {sequence_display} **{word}** : **{count}** 条\n\n"
                    )
                elif count >= 5:
                    word_header = (
                        f"📈 {sequence_display} **{word}** : **{count}** 条\n\n"
                    )
                else:
                    word_header = f"📌 {sequence_display} **{word}** : {count} 条\n\n"
            elif format_type == "telegram":
                if count >= 10:
                    word_header = f"🔥 {sequence_display} {word} : {count} 条\n\n"
                elif count >= 5:
                    word_header = f"📈 {sequence_display} {word} : {count} 条\n\n"
                else:
                    word_header = f"📌 {sequence_display} {word} : {count} 条\n\n"
            elif format_type == "ntfy":
                if count >= 10:
                    word_header = (
                        f"🔥 {sequence_display} **{word}** : **{count}** 条\n\n"
                    )
                elif count >= 5:
                    word_header = (
                        f"📈 {sequence_display} **{word}** : **{count}** 条\n\n"
                    )
                else:
                    word_header = f"📌 {sequence_display} **{word}** : {count} 条\n\n"
            elif format_type == "feishu":
                if count >= 10:
                    word_header = f"🔥 <font color='grey'>{sequence_display}</font> **{word}** : <font color='red'>{count}</font> 条\n\n"
                elif count >= 5:
                    word_header = f"📈 <font color='grey'>{sequence_display}</font> **{word}** : <font color='orange'>{count}</font> 条\n\n"
                else:
                    word_header = f"📌 <font color='grey'>{sequence_display}</font> **{word}** : {count} 条\n\n"
            elif format_type == "dingtalk":
                if count >= 10:
                    word_header = (
                        f"🔥 {sequence_display} **{word}** : **{count}** 条\n\n"
                    )
                elif count >= 5:
                    word_header = (
                        f"📈 {sequence_display} **{word}** : **{count}** 条\n\n"
                    )
                else:
                    word_header = f"📌 {sequence_display} **{word}** : {count} 条\n\n"
            elif format_type == "slack":
                if count >= 10:
                    word_header = (
                        f"🔥 {sequence_display} *{word}* : *{count}* 条\n\n"
                    )
                elif count >= 5:
                    word_header = (
                        f"📈 {sequence_display} *{word}* : *{count}* 条\n\n"
                    )
                else:
                    word_header = f"📌 {sequence_display} *{word}* : {count} 条\n\n"

            # 构建第一条新闻
            # display_mode: keyword=显示来源, platform=显示关键词
            show_source = display_mode == "keyword"
            show_keyword = display_mode == "platform"
            first_news_line = ""
            if stat["titles"]:
                first_title_data = stat["titles"][0]
                if format_type in ("wework", "bark"):
                    formatted_title = format_title_for_platform(
                        "wework", first_title_data, show_source=show_source, show_keyword=show_keyword
                    )
                elif format_type == "telegram":
                    formatted_title = format_title_for_platform(
                        "telegram", first_title_data, show_source=show_source, show_keyword=show_keyword
                    )
                elif format_type == "ntfy":
                    formatted_title = format_title_for_platform(
                        "ntfy", first_title_data, show_source=show_source, show_keyword=show_keyword
                    )
                elif format_type == "feishu":
                    formatted_title = format_title_for_platform(
                        "feishu", first_title_data, show_source=show_source, show_keyword=show_keyword
                    )
                elif format_type == "dingtalk":
                    formatted_title = format_title_for_platform(
                        "dingtalk", first_title_data, show_source=show_source, show_keyword=show_keyword
                    )
                elif format_type == "slack":
                    formatted_title = format_title_for_platform(
                        "slack", first_title_data, show_source=show_source, show_keyword=show_keyword
                    )
                else:
                    formatted_title = f"{first_title_data['title']}"

                first_news_line = f"  1. {formatted_title}\n"
                if len(stat["titles"]) > 1:
                    first_news_line += "\n"

            # 原子性检查：词组标题+第一条新闻必须一起处理
            word_with_first_news = word_header + first_news_line
            test_content = current_batch + word_with_first_news

            if (
                len(test_content.encode("utf-8")) + len(base_footer.encode("utf-8"))
                >= max_bytes
            ):
                # 当前批次容纳不下，开启新批次
                if current_batch_has_content:
                    batches.append(current_batch + base_footer)
                current_batch = base_header + stats_header + word_with_first_news
                current_batch_has_content = True
                start_index = 1
            else:
                current_batch = test_content
                current_batch_has_content = True
                start_index = 1

            # 处理剩余新闻条目
            for j in range(start_index, len(stat["titles"])):
                title_data = stat["titles"][j]
                if format_type in ("wework", "bark"):
                    formatted_title = format_title_for_platform(
                        "wework", title_data, show_source=show_source, show_keyword=show_keyword
                    )
                elif format_type == "telegram":
                    formatted_title = format_title_for_platform(
                        "telegram", title_data, show_source=show_source, show_keyword=show_keyword
                    )
                elif format_type == "ntfy":
                    formatted_title = format_title_for_platform(
                        "ntfy", title_data, show_source=show_source, show_keyword=show_keyword
                    )
                elif format_type == "feishu":
                    formatted_title = format_title_for_platform(
                        "feishu", title_data, show_source=show_source, show_keyword=show_keyword
                    )
                elif format_type == "dingtalk":
                    formatted_title = format_title_for_platform(
                        "dingtalk", title_data, show_source=show_source, show_keyword=show_keyword
                    )
                elif format_type == "slack":
                    formatted_title = format_title_for_platform(
                        "slack", title_data, show_source=show_source, show_keyword=show_keyword
                    )
                else:
                    formatted_title = f"{title_data['title']}"

                news_line = f"  {j + 1}. {formatted_title}\n"
                if j < len(stat["titles"]) - 1:
                    news_line += "\n"

                test_content = current_batch + news_line
                if (
                    len(test_content.encode("utf-8")) + len(base_footer.encode("utf-8"))
                    >= max_bytes
                ):
                    if current_batch_has_content:
                        batches.append(current_batch + base_footer)
                    current_batch = base_header + stats_header + word_header + news_line
                    current_batch_has_content = True
                else:
                    current_batch = test_content
                    current_batch_has_content = True

            # 词组间分隔符
            if i < len(report_data["stats"]) - 1:
                separator = ""
                if format_type in ("wework", "bark"):
                    separator = f"\n\n\n\n"
                elif format_type == "telegram":
                    separator = f"\n\n"
                elif format_type == "ntfy":
                    separator = f"\n\n"
                elif format_type == "feishu":
                    separator = f"\n{feishu_separator}\n\n"
                elif format_type == "dingtalk":
                    separator = f"\n---\n\n"
                elif format_type == "slack":
                    separator = f"\n\n"

                test_content = current_batch + separator
                if (
                    len(test_content.encode("utf-8")) + len(base_footer.encode("utf-8"))
                    < max_bytes
                ):
                    current_batch = test_content

        return current_batch, current_batch_has_content, batches

    # 定义处理新增新闻的函数
    def process_new_titles_section(current_batch, current_batch_has_content, batches):
        """处理新增新闻"""
        if not report_data["new_titles"]:
            return current_batch, current_batch_has_content, batches

        new_header = ""
        if format_type in ("wework", "bark"):
            new_header = f"\n\n\n\n🆕 **本次新增热点新闻** (共 {report_data['total_new_count']} 条)\n\n"
        elif format_type == "telegram":
            new_header = (
                f"\n\n🆕 本次新增热点新闻 (共 {report_data['total_new_count']} 条)\n\n"
            )
        elif format_type == "ntfy":
            new_header = f"\n\n🆕 **本次新增热点新闻** (共 {report_data['total_new_count']} 条)\n\n"
        elif format_type == "feishu":
            new_header = f"\n{feishu_separator}\n\n🆕 **本次新增热点新闻** (共 {report_data['total_new_count']} 条)\n\n"
        elif format_type == "dingtalk":
            new_header = f"\n---\n\n🆕 **本次新增热点新闻** (共 {report_data['total_new_count']} 条)\n\n"
        elif format_type == "slack":
            new_header = f"\n\n🆕 *本次新增热点新闻* (共 {report_data['total_new_count']} 条)\n\n"

        test_content = current_batch + new_header
        if (
            len(test_content.encode("utf-8")) + len(base_footer.encode("utf-8"))
            >= max_bytes
        ):
            if current_batch_has_content:
                batches.append(current_batch + base_footer)
            current_batch = base_header + new_header
            current_batch_has_content = True
        else:
            current_batch = test_content
            current_batch_has_content = True

        # 逐个处理新增新闻来源
        for source_data in report_data["new_titles"]:
            source_header = ""
            if format_type in ("wework", "bark"):
                source_header = f"**{source_data['source_name']}** ({len(source_data['titles'])} 条):\n\n"
            elif format_type == "telegram":
                source_header = f"{source_data['source_name']} ({len(source_data['titles'])} 条):\n\n"
            elif format_type == "ntfy":
                source_header = f"**{source_data['source_name']}** ({len(source_data['titles'])} 条):\n\n"
            elif format_type == "feishu":
                source_header = f"**{source_data['source_name']}** ({len(source_data['titles'])} 条):\n\n"
            elif format_type == "dingtalk":
                source_header = f"**{source_data['source_name']}** ({len(source_data['titles'])} 条):\n\n"
            elif format_type == "slack":
                source_header = f"*{source_data['source_name']}* ({len(source_data['titles'])} 条):\n\n"

            # 构建第一条新增新闻
            first_news_line = ""
            if source_data["titles"]:
                first_title_data = source_data["titles"][0]
                title_data_copy = first_title_data.copy()
                title_data_copy["is_new"] = False

                if format_type in ("wework", "bark"):
                    formatted_title = format_title_for_platform(
                        "wework", title_data_copy, show_source=False
                    )
                elif format_type == "telegram":
                    formatted_title = format_title_for_platform(
                        "telegram", title_data_copy, show_source=False
                    )
                elif format_type == "feishu":
                    formatted_title = format_title_for_platform(
                        "feishu", title_data_copy, show_source=False
                    )
                elif format_type == "dingtalk":
                    formatted_title = format_title_for_platform(
                        "dingtalk", title_data_copy, show_source=False
                    )
                elif format_type == "slack":
                    formatted_title = format_title_for_platform(
                        "slack", title_data_copy, show_source=False
                    )
                else:
                    formatted_title = f"{title_data_copy['title']}"

                first_news_line = f"  1. {formatted_title}\n"

            # 原子性检查：来源标题+第一条新闻
            source_with_first_news = source_header + first_news_line
            test_content = current_batch + source_with_first_news

            if (
                len(test_content.encode("utf-8")) + len(base_footer.encode("utf-8"))
                >= max_bytes
            ):
                if current_batch_has_content:
                    batches.append(current_batch + base_footer)
                current_batch = base_header + new_header + source_with_first_news
                current_batch_has_content = True
                start_index = 1
            else:
                current_batch = test_content
                current_batch_has_content = True
                start_index = 1

            # 处理剩余新增新闻
            for j in range(start_index, len(source_data["titles"])):
                title_data = source_data["titles"][j]
                title_data_copy = title_data.copy()
                title_data_copy["is_new"] = False

                if format_type == "wework":
                    formatted_title = format_title_for_platform(
                        "wework", title_data_copy, show_source=False
                    )
                elif format_type == "telegram":
                    formatted_title = format_title_for_platform(
                        "telegram", title_data_copy, show_source=False
                    )
                elif format_type == "feishu":
                    formatted_title = format_title_for_platform(
                        "feishu", title_data_copy, show_source=False
                    )
                elif format_type == "dingtalk":
                    formatted_title = format_title_for_platform(
                        "dingtalk", title_data_copy, show_source=False
                    )
                elif format_type == "slack":
                    formatted_title = format_title_for_platform(
                        "slack", title_data_copy, show_source=False
                    )
                else:
                    formatted_title = f"{title_data_copy['title']}"

                news_line = f"  {j + 1}. {formatted_title}\n"

                test_content = current_batch + news_line
                if (
                    len(test_content.encode("utf-8")) + len(base_footer.encode("utf-8"))
                    >= max_bytes
                ):
                    if current_batch_has_content:
                        batches.append(current_batch + base_footer)
                    current_batch = base_header + new_header + source_header + news_line
                    current_batch_has_content = True
                else:
                    current_batch = test_content
                    current_batch_has_content = True

            current_batch += "\n"

        return current_batch, current_batch_has_content, batches

    # 根据配置决定处理顺序
    if reverse_content_order:
        # 新增热点在前，热点词汇统计在后
        # 1. 处理热榜新增
        current_batch, current_batch_has_content, batches = process_new_titles_section(
            current_batch, current_batch_has_content, batches
        )
        # 2. 处理 RSS 新增（如果有）
        if rss_new_items:
            current_batch, current_batch_has_content, batches = _process_rss_new_titles_section(
                rss_new_items, format_type, feishu_separator, base_header, base_footer,
                max_bytes, current_batch, current_batch_has_content, batches, timezone
            )
        # 3. 处理热榜统计
        current_batch, current_batch_has_content, batches = process_stats_section(
            current_batch, current_batch_has_content, batches
        )
        # 4. 处理 RSS 统计（如果有）
        if rss_items:
            current_batch, current_batch_has_content, batches = _process_rss_stats_section(
                rss_items, format_type, feishu_separator, base_header, base_footer,
                max_bytes, current_batch, current_batch_has_content, batches, timezone
            )
    else:
        # 默认：热点词汇统计在前，新增热点在后
        # 1. 处理热榜统计
        current_batch, current_batch_has_content, batches = process_stats_section(
            current_batch, current_batch_has_content, batches
        )
        # 2. 处理 RSS 统计（如果有）
        if rss_items:
            current_batch, current_batch_has_content, batches = _process_rss_stats_section(
                rss_items, format_type, feishu_separator, base_header, base_footer,
                max_bytes, current_batch, current_batch_has_content, batches, timezone
            )
        # 3. 处理热榜新增
        current_batch, current_batch_has_content, batches = process_new_titles_section(
            current_batch, current_batch_has_content, batches
        )
        # 4. 处理 RSS 新增（如果有）
        if rss_new_items:
            current_batch, current_batch_has_content, batches = _process_rss_new_titles_section(
                rss_new_items, format_type, feishu_separator, base_header, base_footer,
                max_bytes, current_batch, current_batch_has_content, batches, timezone
            )

    if report_data["failed_ids"]:
        failed_header = ""
        if format_type == "wework":
            failed_header = f"\n\n\n\n⚠️ **数据获取失败的平台：**\n\n"
        elif format_type == "telegram":
            failed_header = f"\n\n⚠️ 数据获取失败的平台：\n\n"
        elif format_type == "ntfy":
            failed_header = f"\n\n⚠️ **数据获取失败的平台：**\n\n"
        elif format_type == "feishu":
            failed_header = f"\n{feishu_separator}\n\n⚠️ **数据获取失败的平台：**\n\n"
        elif format_type == "dingtalk":
            failed_header = f"\n---\n\n⚠️ **数据获取失败的平台：**\n\n"

        test_content = current_batch + failed_header
        if (
            len(test_content.encode("utf-8")) + len(base_footer.encode("utf-8"))
            >= max_bytes
        ):
            if current_batch_has_content:
                batches.append(current_batch + base_footer)
            current_batch = base_header + failed_header
            current_batch_has_content = True
        else:
            current_batch = test_content
            current_batch_has_content = True

        for i, id_value in enumerate(report_data["failed_ids"], 1):
            if format_type == "feishu":
                failed_line = f"  • <font color='red'>{id_value}</font>\n"
            elif format_type == "dingtalk":
                failed_line = f"  • **{id_value}**\n"
            else:
                failed_line = f"  • {id_value}\n"

            test_content = current_batch + failed_line
            if (
                len(test_content.encode("utf-8")) + len(base_footer.encode("utf-8"))
                >= max_bytes
            ):
                if current_batch_has_content:
                    batches.append(current_batch + base_footer)
                current_batch = base_header + failed_header + failed_line
                current_batch_has_content = True
            else:
                current_batch = test_content
                current_batch_has_content = True

    # 完成最后批次
    if current_batch_has_content:
        batches.append(current_batch + base_footer)

    return batches


def _process_rss_stats_section(
    rss_stats: list,
    format_type: str,
    feishu_separator: str,
    base_header: str,
    base_footer: str,
    max_bytes: int,
    current_batch: str,
    current_batch_has_content: bool,
    batches: List[str],
    timezone: str = "Asia/Shanghai",
) -> tuple:
    """处理 RSS 统计区块（按关键词分组，与热榜统计格式一致）

    Args:
        rss_stats: RSS 关键词统计列表，格式与热榜 stats 一致：
            [{"word": "AI", "count": 5, "titles": [...]}]
        format_type: 格式类型
        feishu_separator: 飞书分隔符
        base_header: 基础头部
        base_footer: 基础尾部
        max_bytes: 最大字节数
        current_batch: 当前批次内容
        current_batch_has_content: 当前批次是否有内容
        batches: 已完成的批次列表
        timezone: 时区名称

    Returns:
        (current_batch, current_batch_has_content, batches) 元组
    """
    if not rss_stats:
        return current_batch, current_batch_has_content, batches

    # 计算总条目数
    total_items = sum(stat["count"] for stat in rss_stats)
    total_keywords = len(rss_stats)

    # RSS 统计区块标题
    rss_header = ""
    if format_type == "feishu":
        rss_header = f"\n{feishu_separator}\n\n📰 **RSS 订阅统计** (共 {total_items} 条)\n\n"
    elif format_type == "dingtalk":
        rss_header = f"\n---\n\n📰 **RSS 订阅统计** (共 {total_items} 条)\n\n"
    elif format_type == "telegram":
        rss_header = f"\n\n📰 RSS 订阅统计 (共 {total_items} 条)\n\n"
    elif format_type == "slack":
        rss_header = f"\n\n📰 *RSS 订阅统计* (共 {total_items} 条)\n\n"
    else:
        rss_header = f"\n\n📰 **RSS 订阅统计** (共 {total_items} 条)\n\n"

    # 添加 RSS 标题
    test_content = current_batch + rss_header
    if len(test_content.encode("utf-8")) + len(base_footer.encode("utf-8")) < max_bytes:
        current_batch = test_content
        current_batch_has_content = True
    else:
        if current_batch_has_content:
            batches.append(current_batch + base_footer)
        current_batch = base_header + rss_header
        current_batch_has_content = True

    # 逐个处理关键词组（与热榜一致）
    for i, stat in enumerate(rss_stats):
        word = stat["word"]
        count = stat["count"]
        sequence_display = f"[{i + 1}/{total_keywords}]"

        # 构建关键词标题（与热榜格式一致）
        word_header = ""
        if format_type in ("wework", "bark"):
            if count >= 10:
                word_header = f"🔥 {sequence_display} **{word}** : **{count}** 条\n\n"
            elif count >= 5:
                word_header = f"📈 {sequence_display} **{word}** : **{count}** 条\n\n"
            else:
                word_header = f"📌 {sequence_display} **{word}** : {count} 条\n\n"
        elif format_type == "telegram":
            if count >= 10:
                word_header = f"🔥 {sequence_display} {word} : {count} 条\n\n"
            elif count >= 5:
                word_header = f"📈 {sequence_display} {word} : {count} 条\n\n"
            else:
                word_header = f"📌 {sequence_display} {word} : {count} 条\n\n"
        elif format_type == "ntfy":
            if count >= 10:
                word_header = f"🔥 {sequence_display} **{word}** : **{count}** 条\n\n"
            elif count >= 5:
                word_header = f"📈 {sequence_display} **{word}** : **{count}** 条\n\n"
            else:
                word_header = f"📌 {sequence_display} **{word}** : {count} 条\n\n"
        elif format_type == "feishu":
            if count >= 10:
                word_header = f"🔥 <font color='grey'>{sequence_display}</font> **{word}** : <font color='red'>{count}</font> 条\n\n"
            elif count >= 5:
                word_header = f"📈 <font color='grey'>{sequence_display}</font> **{word}** : <font color='orange'>{count}</font> 条\n\n"
            else:
                word_header = f"📌 <font color='grey'>{sequence_display}</font> **{word}** : {count} 条\n\n"
        elif format_type == "dingtalk":
            if count >= 10:
                word_header = f"🔥 {sequence_display} **{word}** : **{count}** 条\n\n"
            elif count >= 5:
                word_header = f"📈 {sequence_display} **{word}** : **{count}** 条\n\n"
            else:
                word_header = f"📌 {sequence_display} **{word}** : {count} 条\n\n"
        elif format_type == "slack":
            if count >= 10:
                word_header = f"🔥 {sequence_display} *{word}* : *{count}* 条\n\n"
            elif count >= 5:
                word_header = f"📈 {sequence_display} *{word}* : *{count}* 条\n\n"
            else:
                word_header = f"📌 {sequence_display} *{word}* : {count} 条\n\n"

        # 构建第一条新闻（使用 format_title_for_platform）
        first_news_line = ""
        if stat["titles"]:
            first_title_data = stat["titles"][0]
            if format_type in ("wework", "bark"):
                formatted_title = format_title_for_platform("wework", first_title_data, show_source=True)
            elif format_type == "telegram":
                formatted_title = format_title_for_platform("telegram", first_title_data, show_source=True)
            elif format_type == "ntfy":
                formatted_title = format_title_for_platform("ntfy", first_title_data, show_source=True)
            elif format_type == "feishu":
                formatted_title = format_title_for_platform("feishu", first_title_data, show_source=True)
            elif format_type == "dingtalk":
                formatted_title = format_title_for_platform("dingtalk", first_title_data, show_source=True)
            elif format_type == "slack":
                formatted_title = format_title_for_platform("slack", first_title_data, show_source=True)
            else:
                formatted_title = f"{first_title_data['title']}"

            first_news_line = f"  1. {formatted_title}\n"
            if len(stat["titles"]) > 1:
                first_news_line += "\n"

        # 原子性检查：关键词标题 + 第一条新闻必须一起处理
        word_with_first_news = word_header + first_news_line
        test_content = current_batch + word_with_first_news

        if len(test_content.encode("utf-8")) + len(base_footer.encode("utf-8")) >= max_bytes:
            if current_batch_has_content:
                batches.append(current_batch + base_footer)
            current_batch = base_header + rss_header + word_with_first_news
            current_batch_has_content = True
            start_index = 1
        else:
            current_batch = test_content
            current_batch_has_content = True
            start_index = 1

        # 处理剩余新闻条目
        for j in range(start_index, len(stat["titles"])):
            title_data = stat["titles"][j]
            if format_type in ("wework", "bark"):
                formatted_title = format_title_for_platform("wework", title_data, show_source=True)
            elif format_type == "telegram":
                formatted_title = format_title_for_platform("telegram", title_data, show_source=True)
            elif format_type == "ntfy":
                formatted_title = format_title_for_platform("ntfy", title_data, show_source=True)
            elif format_type == "feishu":
                formatted_title = format_title_for_platform("feishu", title_data, show_source=True)
            elif format_type == "dingtalk":
                formatted_title = format_title_for_platform("dingtalk", title_data, show_source=True)
            elif format_type == "slack":
                formatted_title = format_title_for_platform("slack", title_data, show_source=True)
            else:
                formatted_title = f"{title_data['title']}"

            news_line = f"  {j + 1}. {formatted_title}\n"
            if j < len(stat["titles"]) - 1:
                news_line += "\n"

            test_content = current_batch + news_line
            if len(test_content.encode("utf-8")) + len(base_footer.encode("utf-8")) >= max_bytes:
                if current_batch_has_content:
                    batches.append(current_batch + base_footer)
                current_batch = base_header + rss_header + word_header + news_line
                current_batch_has_content = True
            else:
                current_batch = test_content
                current_batch_has_content = True

        # 关键词间分隔符
        if i < len(rss_stats) - 1:
            separator = ""
            if format_type in ("wework", "bark"):
                separator = "\n\n\n\n"
            elif format_type == "telegram":
                separator = "\n\n"
            elif format_type == "ntfy":
                separator = "\n\n"
            elif format_type == "feishu":
                separator = f"\n{feishu_separator}\n\n"
            elif format_type == "dingtalk":
                separator = "\n---\n\n"
            elif format_type == "slack":
                separator = "\n\n"

            test_content = current_batch + separator
            if len(test_content.encode("utf-8")) + len(base_footer.encode("utf-8")) < max_bytes:
                current_batch = test_content

    return current_batch, current_batch_has_content, batches


def _process_rss_new_titles_section(
    rss_new_stats: list,
    format_type: str,
    feishu_separator: str,
    base_header: str,
    base_footer: str,
    max_bytes: int,
    current_batch: str,
    current_batch_has_content: bool,
    batches: List[str],
    timezone: str = "Asia/Shanghai",
) -> tuple:
    """处理 RSS 新增区块（按来源分组，与热榜新增格式一致）

    Args:
        rss_new_stats: RSS 新增关键词统计列表，格式与热榜 stats 一致：
            [{"word": "AI", "count": 5, "titles": [...]}]
        format_type: 格式类型
        feishu_separator: 飞书分隔符
        base_header: 基础头部
        base_footer: 基础尾部
        max_bytes: 最大字节数
        current_batch: 当前批次内容
        current_batch_has_content: 当前批次是否有内容
        batches: 已完成的批次列表
        timezone: 时区名称

    Returns:
        (current_batch, current_batch_has_content, batches) 元组
    """
    if not rss_new_stats:
        return current_batch, current_batch_has_content, batches

    # 从关键词分组中提取所有条目，重新按来源分组
    source_map = {}
    for stat in rss_new_stats:
        for title_data in stat.get("titles", []):
            source_name = title_data.get("source_name", "未知来源")
            if source_name not in source_map:
                source_map[source_name] = []
            source_map[source_name].append(title_data)

    if not source_map:
        return current_batch, current_batch_has_content, batches

    # 计算总条目数
    total_items = sum(len(titles) for titles in source_map.values())

    # RSS 新增区块标题
    new_header = ""
    if format_type in ("wework", "bark"):
        new_header = f"\n\n\n\n🆕 **RSS 本次新增** (共 {total_items} 条)\n\n"
    elif format_type == "telegram":
        new_header = f"\n\n🆕 RSS 本次新增 (共 {total_items} 条)\n\n"
    elif format_type == "ntfy":
        new_header = f"\n\n🆕 **RSS 本次新增** (共 {total_items} 条)\n\n"
    elif format_type == "feishu":
        new_header = f"\n{feishu_separator}\n\n🆕 **RSS 本次新增** (共 {total_items} 条)\n\n"
    elif format_type == "dingtalk":
        new_header = f"\n---\n\n🆕 **RSS 本次新增** (共 {total_items} 条)\n\n"
    elif format_type == "slack":
        new_header = f"\n\n🆕 *RSS 本次新增* (共 {total_items} 条)\n\n"

    # 添加 RSS 新增标题
    test_content = current_batch + new_header
    if len(test_content.encode("utf-8")) + len(base_footer.encode("utf-8")) >= max_bytes:
        if current_batch_has_content:
            batches.append(current_batch + base_footer)
        current_batch = base_header + new_header
        current_batch_has_content = True
    else:
        current_batch = test_content
        current_batch_has_content = True

    # 按来源分组显示（与热榜新增格式一致）
    source_list = list(source_map.items())
    for i, (source_name, titles) in enumerate(source_list):
        count = len(titles)

        # 构建来源标题（与热榜新增格式一致）
        source_header = ""
        if format_type in ("wework", "bark"):
            source_header = f"**{source_name}** ({count} 条):\n\n"
        elif format_type == "telegram":
            source_header = f"{source_name} ({count} 条):\n\n"
        elif format_type == "ntfy":
            source_header = f"**{source_name}** ({count} 条):\n\n"
        elif format_type == "feishu":
            source_header = f"**{source_name}** ({count} 条):\n\n"
        elif format_type == "dingtalk":
            source_header = f"**{source_name}** ({count} 条):\n\n"
        elif format_type == "slack":
            source_header = f"*{source_name}* ({count} 条):\n\n"

        # 构建第一条新闻（不显示来源，禁用 new emoji）
        first_news_line = ""
        if titles:
            first_title_data = titles[0].copy()
            first_title_data["is_new"] = False
            if format_type in ("wework", "bark"):
                formatted_title = format_title_for_platform("wework", first_title_data, show_source=False)
            elif format_type == "telegram":
                formatted_title = format_title_for_platform("telegram", first_title_data, show_source=False)
            elif format_type == "ntfy":
                formatted_title = format_title_for_platform("ntfy", first_title_data, show_source=False)
            elif format_type == "feishu":
                formatted_title = format_title_for_platform("feishu", first_title_data, show_source=False)
            elif format_type == "dingtalk":
                formatted_title = format_title_for_platform("dingtalk", first_title_data, show_source=False)
            elif format_type == "slack":
                formatted_title = format_title_for_platform("slack", first_title_data, show_source=False)
            else:
                formatted_title = f"{first_title_data['title']}"

            first_news_line = f"  1. {formatted_title}\n"

        # 原子性检查：来源标题 + 第一条新闻必须一起处理
        source_with_first_news = source_header + first_news_line
        test_content = current_batch + source_with_first_news

        if len(test_content.encode("utf-8")) + len(base_footer.encode("utf-8")) >= max_bytes:
            if current_batch_has_content:
                batches.append(current_batch + base_footer)
            current_batch = base_header + new_header + source_with_first_news
            current_batch_has_content = True
            start_index = 1
        else:
            current_batch = test_content
            current_batch_has_content = True
            start_index = 1

        # 处理剩余新闻条目（禁用 new emoji）
        for j in range(start_index, len(titles)):
            title_data = titles[j].copy()
            title_data["is_new"] = False
            if format_type in ("wework", "bark"):
                formatted_title = format_title_for_platform("wework", title_data, show_source=False)
            elif format_type == "telegram":
                formatted_title = format_title_for_platform("telegram", title_data, show_source=False)
            elif format_type == "ntfy":
                formatted_title = format_title_for_platform("ntfy", title_data, show_source=False)
            elif format_type == "feishu":
                formatted_title = format_title_for_platform("feishu", title_data, show_source=False)
            elif format_type == "dingtalk":
                formatted_title = format_title_for_platform("dingtalk", title_data, show_source=False)
            elif format_type == "slack":
                formatted_title = format_title_for_platform("slack", title_data, show_source=False)
            else:
                formatted_title = f"{title_data['title']}"

            news_line = f"  {j + 1}. {formatted_title}\n"

            test_content = current_batch + news_line
            if len(test_content.encode("utf-8")) + len(base_footer.encode("utf-8")) >= max_bytes:
                if current_batch_has_content:
                    batches.append(current_batch + base_footer)
                current_batch = base_header + new_header + source_header + news_line
                current_batch_has_content = True
            else:
                current_batch = test_content
                current_batch_has_content = True

        # 来源间添加空行（与热榜新增格式一致）
        current_batch += "\n"

    return current_batch, current_batch_has_content, batches
