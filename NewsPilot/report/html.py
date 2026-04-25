# coding=utf-8
"""
HTML 报告渲染模块

提供 HTML 格式的热点新闻报告生成功能
"""

from datetime import datetime
from typing import Dict, List, Optional, Callable

from NewsPilot.report.helpers import html_escape


def render_html_content(
    report_data: Dict,
    total_titles: int,
    is_daily_summary: bool = False,
    mode: str = "daily",
    update_info: Optional[Dict] = None,
    *,
    reverse_content_order: bool = False,
    get_time_func: Optional[Callable[[], datetime]] = None,
    rss_items: Optional[List[Dict]] = None,
    rss_new_items: Optional[List[Dict]] = None,
    show_rss_new_items: bool = True,
    display_mode: str = "keyword",
    hide_rss_without_llm: bool = False,
    os_ai_top_items: Optional[List[Dict]] = None,
    os_ai_key_point: str = "",
) -> str:
    """渲染HTML内容

    Args:
        report_data: 报告数据字典，包含 stats, new_titles, failed_ids, total_new_count
        total_titles: 新闻总数
        is_daily_summary: 是否为当日汇总
        mode: 报告模式 ("daily", "current", "incremental")
        update_info: 更新信息（可选）
        reverse_content_order: 是否反转内容顺序（新增热点在前）
        get_time_func: 获取当前时间的函数（可选，默认使用 datetime.now）
        rss_items: RSS 统计条目列表（可选）
        rss_new_items: RSS 新增条目列表（可选）
        display_mode: 显示模式 ("keyword"=按关键词分组, "platform"=按平台分组)

    Returns:
        渲染后的 HTML 字符串
    """
    def _count_titles(stats: Optional[List[Dict]]) -> int:
        if not stats:
            return 0
        total = 0
        for stat in stats:
            if not isinstance(stat, dict):
                continue
            titles = stat.get("titles") or []
            if isinstance(titles, list):
                total += len(titles)
        return total

    rss_display_count = _count_titles(rss_items)
    effective_total_titles = int(total_titles or 0) + rss_display_count
    effective_hot_news_count = _count_titles(report_data.get("stats")) + rss_display_count

    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>热点新闻分析</title>
        <script src="https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js" integrity="sha512-BNaRQnYJYiPSqHHDb58B0yaPfCu+Wgds8Gp/gU33kqBtgNS4tSPHuGibyoeqMV/TJlSKda6FXzoEyYGjTe+vXA==" crossorigin="anonymous" referrerpolicy="no-referrer"></script>
        <style>
            :root {
                --primary-gradient: linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%);
                --bg-color: #f3f4f6;
                --card-bg: #ffffff;
                --text-main: #1f2937;
                --text-secondary: #6b7280;
                --text-light: #9ca3af;
                --accent-blue: #3b82f6;
                --accent-red: #ef4444;
                --accent-orange: #f97316;
                --accent-green: #10b981;
                --border-color: #e5e7eb;
            }

            * { box-sizing: border-box; }
            
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
                margin: 0;
                padding: 20px;
                background: var(--bg-color);
                color: var(--text-main);
                line-height: 1.6;
                -webkit-font-smoothing: antialiased;
            }

            .container {
                max-width: 680px;
                margin: 0 auto;
                background: var(--card-bg);
                border-radius: 20px;
                overflow: hidden;
                box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.1), 0 8px 10px -6px rgba(0, 0, 0, 0.1);
            }

            .header {
                background: var(--primary-gradient);
                color: white;
                padding: 40px 32px;
                text-align: center;
                position: relative;
            }

            .save-buttons {
                display: flex;
                flex-wrap: wrap;
                gap: 10px;
                justify-content: center;
                padding: 12px 20px;
                background: var(--primary-gradient);
                border-top: 1px solid rgba(255, 255, 255, 0.15);
            }

            .save-btn {
                background: rgba(255, 255, 255, 0.15);
                border: 1px solid rgba(255, 255, 255, 0.2);
                color: white;
                padding: 8px 16px;
                border-radius: 20px;
                cursor: pointer;
                font-size: 13px;
                font-weight: 500;
                transition: all 0.2s ease;
                backdrop-filter: blur(8px);
                display: flex;
                align-items: center;
                gap: 6px;
            }

            .save-btn:hover {
                background: rgba(255, 255, 255, 0.25);
                transform: translateY(-1px);
            }

            .save-btn:active {
                transform: translateY(0);
            }

            .save-btn:disabled {
                opacity: 0.6;
                cursor: not-allowed;
            }

            /* Weekly Export Selection */
            .ia-selectable { position: relative; }
            .ia-pick {
                position: absolute;
                top: 12px;
                right: 12px;
                display: none;
                align-items: center;
                gap: 8px;
                padding: 6px 10px;
                border-radius: 999px;
                background: rgba(17, 24, 39, 0.86);
                border: 1px solid rgba(255, 255, 255, 0.16);
                color: rgba(255, 255, 255, 0.95);
                font-size: 12px;
                font-weight: 600;
                z-index: 5;
                backdrop-filter: blur(8px);
            }
            .ia-pick input {
                width: 16px;
                height: 16px;
                margin: 0;
            }
            body.select-mode .ia-pick { display: flex; }
            .ia-selected {
                outline: 2px solid rgba(99, 102, 241, 0.9);
                box-shadow: 0 0 0 4px rgba(99, 102, 241, 0.16);
            }

            .header-title {
                font-size: 28px;
                font-weight: 800;
                margin: 0 0 24px 0;
                letter-spacing: -0.5px;
                text-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }

            .header-info {
                display: grid;
                grid-template-columns: repeat(4, 1fr);
                gap: 16px;
                background: rgba(255, 255, 255, 0.1);
                border-radius: 16px;
                padding: 16px;
                backdrop-filter: blur(4px);
            }

            .info-item {
                display: flex;
                flex-direction: column;
                align-items: center;
            }

            .info-label {
                font-size: 12px;
                opacity: 0.8;
                margin-bottom: 4px;
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }

            .info-value {
                font-weight: 700;
                font-size: 18px;
            }

            .content {
                padding: 32px;
            }

            .word-group {
                margin-bottom: 40px;
                background: #f8fafc;
                border-radius: 16px;
                padding: 20px;
                border: 1px solid #f1f5f9;
            }

            .word-group:last-child {
                margin-bottom: 0;
            }

            .word-header {
                display: flex;
                align-items: center;
                justify-content: space-between;
                margin-bottom: 16px;
                padding-bottom: 12px;
                border-bottom: 2px solid #e2e8f0;
            }

            .word-info {
                display: flex;
                align-items: center;
                gap: 12px;
            }

            .word-name {
                font-size: 20px;
                font-weight: 700;
                color: var(--text-main);
                letter-spacing: -0.3px;
            }

            .word-count {
                padding: 4px 10px;
                border-radius: 20px;
                font-size: 12px;
                font-weight: 600;
                background: #e5e7eb;
                color: var(--text-secondary);
            }

            .word-count.hot { background: #fee2e2; color: #dc2626; }
            .word-count.warm { background: #ffedd5; color: #ea580c; }

            .word-index {
                font-family: 'SF Mono', Consolas, monospace;
                color: var(--text-light);
                font-size: 12px;
            }

            .news-item {
                background: white;
                border-radius: 12px;
                padding: 16px;
                margin-bottom: 12px;
                box-shadow: 0 1px 3px rgba(0,0,0,0.05);
                display: flex;
                gap: 16px;
                transition: transform 0.2s ease, box-shadow 0.2s ease;
                border: 1px solid transparent;
            }

            .news-item:hover {
                transform: translateY(-1px);
                box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);
                border-color: #e5e7eb;
            }

            .news-item:last-child {
                margin-bottom: 0;
            }

            .news-item.new {
                background: #fffbeb;
                border: 1px solid #fcd34d;
            }

            .news-number {
                font-size: 14px;
                font-weight: 700;
                color: var(--text-light);
                min-width: 24px;
                height: 24px;
                display: flex;
                align-items: center;
                justify-content: center;
                background: #f3f4f6;
                border-radius: 8px;
                flex-shrink: 0;
                margin-top: 2px;
            }

            .news-content {
                flex: 1;
                min-width: 0;
            }

            .news-header {
                display: flex;
                align-items: center;
                gap: 8px;
                margin-bottom: 6px;
                flex-wrap: wrap;
            }

            .source-name {
                font-size: 12px;
                font-weight: 600;
                color: var(--text-secondary);
                background: #f3f4f6;
                padding: 2px 8px;
                border-radius: 4px;
            }

            .keyword-tag {
                color: var(--accent-blue);
                font-size: 12px;
                font-weight: 600;
                background: #eff6ff;
                padding: 2px 8px;
                border-radius: 4px;
            }

            .rank-num {
                font-size: 11px;
                font-weight: 700;
                padding: 2px 8px;
                border-radius: 12px;
                color: white;
                background: #9ca3af;
                height: 20px;
                display: flex;
                align-items: center;
            }

            .rank-num.top { background: var(--accent-red); box-shadow: 0 2px 4px rgba(239, 68, 68, 0.3); }
            .rank-num.high { background: var(--accent-orange); }

            .time-info {
                color: var(--text-light);
                font-size: 11px;
                margin-left: auto;
            }

            .news-title {
                font-size: 16px;
                line-height: 1.5;
                color: var(--text-main);
                font-weight: 500;
                margin: 0;
            }

            .news-link {
                color: var(--text-main);
                text-decoration: none;
                transition: color 0.2s;
            }

            .news-link:hover {
                color: var(--accent-blue);
            }

            /* New Section Styling */
            .new-section {
                margin-top: 48px;
                padding-top: 32px;
                border-top: 2px dashed #e5e7eb;
            }

            .new-section-title {
                font-size: 18px;
                font-weight: 700;
                color: var(--text-main);
                margin: 0 0 24px 0;
                display: flex;
                align-items: center;
                gap: 8px;
            }

            .new-section-title::before {
                content: '';
                display: block;
                width: 4px;
                height: 18px;
                background: var(--accent-orange);
                border-radius: 2px;
            }

            .new-source-group {
                background: white;
                border-radius: 16px;
                padding: 20px;
                margin-bottom: 24px;
                box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05);
                border: 1px solid #f3f4f6;
            }

            .new-source-title {
                font-size: 14px;
                font-weight: 600;
                color: var(--text-secondary);
                margin-bottom: 16px;
                padding-bottom: 8px;
                border-bottom: 1px solid #f3f4f6;
            }

            .new-item {
                display: flex;
                align-items: flex-start;
                gap: 12px;
                padding: 10px 0;
                border-bottom: 1px solid #f9fafb;
            }

            .new-item:last-child {
                border-bottom: none;
            }

            .new-item-number {
                font-size: 12px;
                color: var(--text-light);
                width: 20px;
                text-align: center;
                margin-top: 3px;
            }

            .new-item-rank {
                font-size: 10px;
                font-weight: 700;
                padding: 2px 6px;
                border-radius: 6px;
                min-width: 24px;
                text-align: center;
                flex-shrink: 0;
                color: white;
                background: #9ca3af;
                margin-top: 4px;
            }
            .new-item-rank.top { background: var(--accent-red); }
            .new-item-rank.high { background: var(--accent-orange); }

            .new-item-content {
                flex: 1;
                min-width: 0;
            }

            .new-item-title {
                font-size: 14px;
                line-height: 1.5;
                color: var(--text-main);
                margin: 0;
            }

            /* RSS Section Styling */
            .rss-section {
                margin-top: 48px;
                padding-top: 32px;
                border-top: 2px dashed #e5e7eb;
            }

            /* OS+AI Top Picks */
            .os-ai-top {
                margin-bottom: 28px;
                background: linear-gradient(to right bottom, #ecfeff, #eef2ff);
                border: 1px solid #dbeafe;
                border-radius: 16px;
                padding: 18px;
                box-shadow: 0 4px 10px -4px rgba(37, 99, 235, 0.18);
            }
            .os-ai-top-header {
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 12px;
                margin-bottom: 14px;
            }
            .os-ai-top-title {
                font-size: 16px;
                font-weight: 800;
                color: #1e3a8a;
                display: flex;
                align-items: center;
                gap: 8px;
                letter-spacing: -0.2px;
            }
            .os-ai-top-title::before {
                content: '🧠';
                font-size: 18px;
            }
            .os-ai-top-sub {
                font-size: 12px;
                color: #475569;
                font-weight: 600;
                background: rgba(255,255,255,0.65);
                border: 1px solid rgba(255,255,255,0.8);
                padding: 4px 10px;
                border-radius: 999px;
                white-space: nowrap;
            }
            .os-ai-top-item {
                background: rgba(255, 255, 255, 0.86);
                border: 1px solid rgba(226, 232, 240, 0.9);
                border-radius: 14px;
                padding: 14px 14px 12px 14px;
                margin-bottom: 10px;
            }
            .os-ai-top-item:last-child { margin-bottom: 0; }
            .os-ai-top-meta {
                display: flex;
                align-items: center;
                gap: 10px;
                flex-wrap: wrap;
                margin-bottom: 8px;
            }
            .os-ai-top-badge {
                font-size: 11px;
                font-weight: 800;
                color: white;
                background: #2563eb;
                padding: 2px 8px;
                border-radius: 999px;
            }
            .os-ai-top-score {
                font-size: 11px;
                font-weight: 800;
                color: #0f172a;
                background: #e0e7ff;
                padding: 2px 8px;
                border-radius: 999px;
            }
            .os-ai-top-reason {
                font-size: 13px;
                color: #334155;
                line-height: 1.55;
                margin: 8px 0 0 0;
                padding-left: 10px;
                border-left: 3px solid #60a5fa;
            }

            .rss-section-header {
                display: flex;
                align-items: center;
                justify-content: space-between;
                margin-bottom: 24px;
            }

            .rss-section-title {
                font-size: 20px;
                font-weight: 700;
                color: #4f46e5; /* Indigo-600 */
                display: flex;
                align-items: center;
                gap: 8px;
                letter-spacing: -0.5px;
            }
            
            .rss-section-title::before {
                content: '🤖';
                font-size: 22px;
            }

            .feed-group {
                margin-bottom: 24px;
            }

            .feed-header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 16px;
                padding-left: 12px;
                border-left: 4px solid #6366f1; /* Indigo-500 */
            }

            .feed-name {
                color: #3730a3; /* Indigo-800 */
                font-weight: 700;
                font-size: 16px;
            }

            .rss-item {
                background: white;
                padding: 20px;
                border-radius: 16px;
                margin-bottom: 16px;
                box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -1px rgba(0, 0, 0, 0.03);
                border: 1px solid #f3f4f6;
                transition: transform 0.2s ease;
            }

            .rss-item:hover {
                transform: translateY(-2px);
                box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.08);
            }

            .rss-meta {
                display: flex;
                align-items: center;
                gap: 12px;
                margin-bottom: 10px;
                flex-wrap: wrap;
            }

            .rss-time {
                color: #9ca3af;
                font-size: 12px;
                font-family: 'SF Mono', Consolas, monospace;
            }

            .rss-author {
                color: #6b7280;
                font-size: 12px;
                font-weight: 500;
                background: #f3f4f6;
                padding: 2px 8px;
                border-radius: 4px;
            }

            .rss-title {
                font-size: 16px;
                font-weight: 600;
                color: #111827;
                margin-bottom: 12px;
                line-height: 1.5;
            }
            
            .rss-title a {
                text-decoration: none;
                color: inherit;
                background-image: linear-gradient(transparent 90%, #e0e7ff 90%); /* Subtle underline effect */
                background-size: 100% 100%;
                background-repeat: no-repeat;
                transition: background-size 0.2s ease;
            }
            
            .rss-title a:hover {
                color: #4f46e5;
            }

            /* AI Insight Box */
            .ai-insight-box {
                margin-top: 16px;
                background: linear-gradient(to right bottom, #f5f3ff, #eff6ff); /* Violet to Blue tint */
                border: 1px solid #e0e7ff;
                border-radius: 12px;
                padding: 16px;
                position: relative;
                overflow: hidden;
            }

            .ai-insight-box::before {
                content: "AI 深度解析";
                position: absolute;
                top: 0;
                right: 0;
                background: linear-gradient(135deg, #6366f1, #8b5cf6);
                color: white;
                font-size: 10px;
                font-weight: 700;
                padding: 4px 10px;
                border-bottom-left-radius: 12px;
                box-shadow: -2px 2px 4px rgba(99, 102, 241, 0.2);
            }

            .ai-title {
                font-size: 14px;
                font-weight: 700;
                color: #4338ca; /* Indigo-700 */
                margin: 0 0 10px 0;
                display: flex;
                align-items: center;
                gap: 6px;
            }
            
            .ai-title::before {
                content: '💡';
                font-size: 14px;
            }

            .ai-summary {
                font-size: 13px;
                color: #4b5563; /* Gray-600 */
                line-height: 1.6;
                background: rgba(255,255,255,0.7);
                padding: 12px;
                border-radius: 8px;
                margin: 0 0 10px 0;
                border: 1px solid rgba(255,255,255,0.5);
            }

            .ai-viewpoint {
                font-size: 13px;
                color: #374151; /* Gray-700 */
                line-height: 1.5;
                margin: 0;
                padding-left: 12px;
                border-left: 3px solid #8b5cf6; /* Violet-500 */
                font-style: italic;
            }

            /* Footer */
            .footer {
                margin-top: 40px;
                padding: 24px;
                background: #f8fafc;
                border-top: 1px solid #e2e8f0;
                text-align: center;
                color: var(--text-light);
            }

            /* Error Section */
            .error-section {
                background: #fef2f2;
                border: 1px solid #fee2e2;
                border-radius: 12px;
                padding: 16px;
                margin-bottom: 24px;
                color: #991b1b;
            }

            /* Mobile Optimizations */
            @media (max-width: 480px) {
                body { padding: 12px; }
                .header { padding: 32px 20px; }
                .header-info { grid-template-columns: 1fr 1fr; }
                .content { padding: 20px; }
                
            .save-btn {
                    flex: 1 1 auto;
                    justify-content: center;
                    background: rgba(255, 255, 255, 0.2);
                }
                
                .word-group { padding: 16px; }
                .news-item { flex-direction: column; gap: 8px; }
                .news-number { align-self: flex-start; }
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <div class="header-title">热点新闻分析</div>
                <div class="header-info">
                    <div class="info-item">
                        <span class="info-label">报告类型</span>
                        <span class="info-value">"""

    # 处理报告类型显示
    if is_daily_summary:
        if mode == "current":
            html += "当前榜单"
        elif mode == "incremental":
            html += "增量模式"
        else:
            html += "当日汇总"
    else:
        html += "实时分析"

    html += """</span>
                    </div>
                    <div class="info-item">
                        <span class="info-label">新闻总数</span>
                        <span class="info-value">"""

    html += f"{effective_total_titles} 条"

    html += """</span>
                    </div>
                    <div class="info-item">
                        <span class="info-label">热点新闻</span>
                        <span class="info-value">"""

    html += f"{effective_hot_news_count} 条"

    html += """</span>
                    </div>
                    <div class="info-item">
                        <span class="info-label">生成时间</span>
                        <span class="info-value">"""

    # 使用提供的时间函数或默认 datetime.now
    if get_time_func:
        now = get_time_func()
    else:
        now = datetime.now()
    html += now.strftime("%m-%d %H:%M")

    html += """</span>
                    </div>
                </div>
            </div>

            <div class="save-buttons">
                <button class="save-btn" onclick="saveAsImage()">保存为图片</button>
                <button class="save-btn" onclick="saveAsMultipleImages()">分段保存</button>
                <button class="save-btn" id="iaToggleSelectBtn" onclick="toggleSelectionMode()">选择资讯</button>
                <button class="save-btn" id="iaClearSelectBtn" onclick="clearSelections()" disabled>清空选择</button>
                <button class="save-btn" id="iaExportWeeklyBtn" onclick="exportWeeklyDigest()" disabled>生成周报</button>
            </div>

            <div class="content">"""

    # 处理失败ID错误信息
    if report_data["failed_ids"]:
        html += """
                <div class="error-section">
                    <div class="error-title">⚠️ 请求失败的平台</div>
                    <ul class="error-list">"""
        for id_value in report_data["failed_ids"]:
            html += f'<li class="error-item">{html_escape(id_value)}</li>'
        html += """
                    </ul>
                </div>"""

    # OS + AI Top Picks 置顶（来自 LLM 自动挑选的 top_items）
    if os_ai_top_items and isinstance(os_ai_top_items, list):
        cleaned = []
        for it in os_ai_top_items:
            if not isinstance(it, dict):
                continue
            title = (it.get("title") or "").strip()
            url = (it.get("url") or "").strip()
            comment = (it.get("comment") or "").strip()
            summary = (it.get("summary") or "").strip()
            viewpoint = (it.get("viewpoint") or "").strip()
            final_score = it.get("final_score")
            if not title:
                continue
            cleaned.append(
                {
                    "title": title,
                    "url": url,
                    "comment": comment,
                    "summary": summary,
                    "viewpoint": viewpoint,
                    "final_score": final_score,
                }
            )

        if cleaned:
            html += """
                <div class="os-ai-top">
                    <div class="os-ai-top-header">
                        <div class="os-ai-top-title">本周 OS+AI 置顶 3 条</div>
                        <div class="os-ai-top-sub">自动筛选 · 可直接周报</div>
                    </div>
            """
            if os_ai_key_point:
                html += f'<div class="os-ai-top-reason">要点：{html_escape(os_ai_key_point)}</div>'
            for idx, it in enumerate(cleaned, start=1):
                title = html_escape(it["title"])
                url = html_escape(it["url"]) if it["url"] else ""
                comment = html_escape(it["comment"]) if it["comment"] else ""
                summary = html_escape(it["summary"]) if it.get("summary") else ""
                viewpoint = html_escape(it["viewpoint"]) if it.get("viewpoint") else ""
                score = it.get("final_score")
                score_text = ""
                try:
                    if score is not None:
                        score_text = f"{float(score):.1f}"
                except Exception:
                    score_text = ""

                html += '<div class="os-ai-top-item">'
                html += '<div class="os-ai-top-meta">'
                html += f'<span class="os-ai-top-badge">TOP {idx}</span>'
                if score_text:
                    html += f'<span class="os-ai-top-score">综合分 {score_text}</span>'
                html += "</div>"
                html += '<div class="rss-title">'
                if url:
                    html += f'<a href="{url}" target="_blank" class="rss-link">{title}</a>'
                else:
                    html += title
                html += "</div>"
                if summary or viewpoint:
                    html += '<div class="ai-insight-box">'
                    if summary:
                        html += f'<div class="ai-summary">{summary}</div>'
                    if viewpoint:
                        html += f'<div class="ai-viewpoint">{viewpoint}</div>'
                    html += "</div>"
                if comment:
                    html += f'<div class="os-ai-top-reason">{comment}</div>'
                html += "</div>"

            html += """
                </div>
            """

    # 生成热点词汇统计部分的HTML
    stats_html = ""
    if report_data["stats"]:
        total_count = len(report_data["stats"])

        for i, stat in enumerate(report_data["stats"], 1):
            count = stat["count"]

            # 确定热度等级
            if count >= 10:
                count_class = "hot"
            elif count >= 5:
                count_class = "warm"
            else:
                count_class = ""

            escaped_word = html_escape(stat["word"])

            stats_html += f"""
                <div class="word-group">
                    <div class="word-header">
                        <div class="word-info">
                            <div class="word-name">{escaped_word}</div>
                            <div class="word-count {count_class}">{count} 条</div>
                        </div>
                        <div class="word-index">{i}/{total_count}</div>
                    </div>"""

            # 处理每个词组下的新闻标题，给每条新闻标上序号
            for j, title_data in enumerate(stat["titles"], 1):
                is_new = title_data.get("is_new", False)
                new_class = "new" if is_new else ""

                stats_html += f"""
                    <div class="news-item {new_class}">
                        <div class="news-number">{j}</div>
                        <div class="news-content">
                            <div class="news-header">"""

                # 根据 display_mode 决定显示来源还是关键词
                if display_mode == "keyword":
                    # keyword 模式：显示来源
                    stats_html += f'<span class="source-name">{html_escape(title_data["source_name"])}</span>'
                else:
                    # platform 模式：显示关键词
                    matched_keyword = title_data.get("matched_keyword", "")
                    if matched_keyword:
                        stats_html += f'<span class="keyword-tag">[{html_escape(matched_keyword)}]</span>'

                # 处理排名显示
                ranks = title_data.get("ranks", [])
                if ranks:
                    min_rank = min(ranks)
                    max_rank = max(ranks)
                    rank_threshold = title_data.get("rank_threshold", 10)

                    # 确定排名等级
                    if min_rank <= 3:
                        rank_class = "top"
                    elif min_rank <= rank_threshold:
                        rank_class = "high"
                    else:
                        rank_class = ""

                    if min_rank == max_rank:
                        rank_text = str(min_rank)
                    else:
                        rank_text = f"{min_rank}-{max_rank}"

                    stats_html += f'<span class="rank-num {rank_class}">{rank_text}</span>'

                # 处理时间显示
                time_display = title_data.get("time_display", "")
                if time_display:
                    # 简化时间显示格式，将波浪线替换为~
                    simplified_time = (
                        time_display.replace(" ~ ", "~")
                        .replace("[", "")
                        .replace("]", "")
                    )
                    stats_html += (
                        f'<span class="time-info">{html_escape(simplified_time)}</span>'
                    )

                # 处理出现次数
                count_info = title_data.get("count", 1)
                if count_info > 1:
                    stats_html += f'<span class="count-info">{count_info}次</span>'

                stats_html += """
                            </div>
                            <div class="news-title">"""

                # 处理标题和链接
                escaped_title = html_escape(title_data["title"])
                link_url = title_data.get("mobile_url") or title_data.get("url", "")

                if link_url:
                    escaped_url = html_escape(link_url)
                    stats_html += f'<a href="{escaped_url}" target="_blank" class="news-link">{escaped_title}</a>'
                else:
                    stats_html += escaped_title

                stats_html += """
                            </div>
                        </div>
                    </div>"""

            stats_html += """
                </div>"""

    # 生成新增新闻区域的HTML
    new_titles_html = ""
    if report_data["new_titles"]:
        new_titles_html += f"""
                <div class="new-section">
                    <div class="new-section-title">本次新增热点 (共 {report_data['total_new_count']} 条)</div>"""

        for source_data in report_data["new_titles"]:
            escaped_source = html_escape(source_data["source_name"])
            titles_count = len(source_data["titles"])

            new_titles_html += f"""
                    <div class="new-source-group">
                        <div class="new-source-title">{escaped_source} · {titles_count}条</div>"""

            # 为新增新闻也添加序号
            for idx, title_data in enumerate(source_data["titles"], 1):
                ranks = title_data.get("ranks", [])

                # 处理新增新闻的排名显示
                rank_class = ""
                if ranks:
                    min_rank = min(ranks)
                    if min_rank <= 3:
                        rank_class = "top"
                    elif min_rank <= title_data.get("rank_threshold", 10):
                        rank_class = "high"

                    if len(ranks) == 1:
                        rank_text = str(ranks[0])
                    else:
                        rank_text = f"{min(ranks)}-{max(ranks)}"
                else:
                    rank_text = "?"

                new_titles_html += f"""
                        <div class="new-item">
                            <div class="new-item-number">{idx}</div>
                            <div class="new-item-rank {rank_class}">{rank_text}</div>
                            <div class="new-item-content">
                                <div class="new-item-title">"""

                # 处理新增新闻的链接
                escaped_title = html_escape(title_data["title"])
                link_url = title_data.get("mobile_url") or title_data.get("url", "")

                if link_url:
                    escaped_url = html_escape(link_url)
                    new_titles_html += f'<a href="{escaped_url}" target="_blank" class="news-link">{escaped_title}</a>'
                else:
                    new_titles_html += escaped_title

                new_titles_html += """
                                </div>
                            </div>
                        </div>"""

            new_titles_html += """
                    </div>"""

        new_titles_html += """
                </div>"""

    # 生成 RSS 统计内容
    def render_rss_stats_html(stats: List[Dict], title: str = "RSS 订阅更新") -> str:
        """渲染 RSS 统计区块 HTML

        Args:
            stats: RSS 分组统计列表，格式与热榜一致：
                [
                    {
                        "word": "关键词",
                        "count": 5,
                        "titles": [
                            {
                                "title": "标题",
                                "source_name": "Feed 名称",
                                "time_display": "12-29 08:20",
                                "url": "...",
                                "is_new": True/False
                            }
                        ]
                    }
                ]
            title: 区块标题

        Returns:
            渲染后的 HTML 字符串
        """
        if not stats:
            return ""

        def _has_llm_insight(title_data: Dict) -> bool:
            return bool(
                (title_data.get("llm_title") or "").strip()
                or (title_data.get("llm_summary") or "").strip()
                or (title_data.get("llm_viewpoint") or "").strip()
            )

        # 计算总条目数
        render_stats: List[Dict] = []
        for stat in stats:
            if not isinstance(stat, dict):
                continue
            titles = stat.get("titles", []) or []
            if not isinstance(titles, list):
                titles = []
            if hide_rss_without_llm:
                titles = [t for t in titles if isinstance(t, dict) and _has_llm_insight(t)]
            if not titles:
                continue
            render_stats.append({**stat, "titles": titles})

        total_count = sum(len(stat.get("titles") or []) for stat in render_stats)
        if total_count == 0:
            return ""

        rss_html = f"""
                <div class="rss-section">
                    <div class="rss-section-header">
                        <div class="rss-section-title">{title}</div>
                        <div class="rss-section-count">{total_count} 条</div>
                    </div>"""

        # 按关键词分组渲染（与热榜格式一致）
        for stat in render_stats:
            keyword = stat.get("word", "")
            titles = stat.get("titles", [])
            if not titles:
                continue

            keyword_count = len(titles)

            rss_html += f"""
                    <div class="feed-group">
                        <div class="feed-header">
                            <div class="feed-name">{html_escape(keyword)}</div>
                            <div class="feed-count">{keyword_count} 条</div>
                        </div>"""

            for title_data in titles:
                item_title = title_data.get("title", "")
                url = title_data.get("url", "")
                time_display = title_data.get("time_display", "")
                source_name = title_data.get("source_name", "")
                is_new = title_data.get("is_new", False)
                llm_title = title_data.get("llm_title", "")
                llm_summary = title_data.get("llm_summary", "")
                llm_viewpoint = title_data.get("llm_viewpoint", "")
                rss_summary = title_data.get("summary", "")

                rss_html += """
                        <div class="rss-item">
                            <div class="rss-meta">"""

                if time_display:
                    rss_html += f'<span class="rss-time">{html_escape(time_display)}</span>'

                if source_name:
                    rss_html += f'<span class="rss-author">{html_escape(source_name)}</span>'

                if is_new:
                    rss_html += '<span class="rss-author" style="color: #dc2626;">NEW</span>'

                rss_html += """
                            </div>
                            <div class="rss-title">"""

                escaped_title = html_escape(item_title)
                if url:
                    escaped_url = html_escape(url)
                    rss_html += f'<a href="{escaped_url}" target="_blank" class="rss-link">{escaped_title}</a>'
                else:
                    rss_html += escaped_title

                rss_html += """
                            </div>
"""

                ai_title = (llm_title or "").strip()
                ai_summary = (llm_summary or "").strip()
                ai_viewpoint = (llm_viewpoint or "").strip()
                rss_summary_text = (rss_summary or "").strip()

                if (ai_summary or ai_viewpoint) and not ai_title:
                    ai_title = (item_title or "").strip()
                if not ai_summary and rss_summary_text:
                    ai_summary = f"RSS 摘要：{rss_summary_text}"

                if ai_title or ai_summary or ai_viewpoint:
                    rss_html += '<div class="ai-insight-box">'
                    
                    if ai_title:
                        rss_html += f'<div class="ai-title">{html_escape(ai_title)}</div>'
                    
                    if ai_summary:
                        rss_html += f'<div class="ai-summary">{html_escape(ai_summary)}</div>'
                    
                    if ai_viewpoint:
                        rss_html += f'<div class="ai-viewpoint">{html_escape(ai_viewpoint)}</div>'
                        
                    rss_html += '</div>'

                rss_html += """
                        </div>"""

            rss_html += """
                    </div>"""

        rss_html += """
                </div>"""
        return rss_html

    # 生成 RSS 统计和新增 HTML
    rss_stats_html = render_rss_stats_html(rss_items, "RSS 订阅更新") if rss_items else ""
    rss_new_html = render_rss_stats_html(rss_new_items, "RSS 新增更新") if (show_rss_new_items and rss_new_items) else ""

    # 根据配置决定内容顺序（与推送逻辑一致）
    if reverse_content_order:
        # 新增在前，统计在后
        # 顺序：热榜新增 → RSS新增 → 热榜统计 → RSS统计
        html += new_titles_html + rss_new_html + stats_html + rss_stats_html
    else:
        # 默认：统计在前，新增在后
        # 顺序：热榜统计 → RSS统计 → 热榜新增 → RSS新增
        html += stats_html + rss_stats_html + new_titles_html + rss_new_html

    html += """
            </div>

            <div class="footer">
                <div class="footer-content">
                    由 <span class="project-name">AIOS</span> 生成
                    """

    if update_info:
        html += f"""
                    <br>
                    <span style="color: #ea580c; font-weight: 500;">
                        发现新版本 {update_info['remote_version']}，当前版本 {update_info['current_version']}
                    </span>"""

    html += """
                </div>
            </div>
        </div>

        <script>
            async function saveAsImage() {
                const button = event.target;
                const originalText = button.textContent;

                try {
                    button.textContent = '生成中...';
                    button.disabled = true;
                    window.scrollTo(0, 0);

                    // 等待页面稳定
                    await new Promise(resolve => setTimeout(resolve, 200));

                    // 截图前隐藏按钮
                    const buttons = document.querySelector('.save-buttons');
                    buttons.style.visibility = 'hidden';

                    // 再次等待确保按钮完全隐藏
                    await new Promise(resolve => setTimeout(resolve, 100));

                    const container = document.querySelector('.container');

                    const canvas = await html2canvas(container, {
                        backgroundColor: '#ffffff',
                        scale: 1.5,
                        useCORS: true,
                        allowTaint: false,
                        imageTimeout: 10000,
                        removeContainer: false,
                        foreignObjectRendering: false,
                        logging: false,
                        width: container.offsetWidth,
                        height: container.offsetHeight,
                        x: 0,
                        y: 0,
                        scrollX: 0,
                        scrollY: 0,
                        windowWidth: window.innerWidth,
                        windowHeight: window.innerHeight
                    });

                    buttons.style.visibility = 'visible';

                    const link = document.createElement('a');
                    const now = new Date();
                    const filename = `NewsPilot_热点新闻分析_${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, '0')}${String(now.getDate()).padStart(2, '0')}_${String(now.getHours()).padStart(2, '0')}${String(now.getMinutes()).padStart(2, '0')}.png`;

                    link.download = filename;
                    link.href = canvas.toDataURL('image/png', 1.0);

                    // 触发下载
                    document.body.appendChild(link);
                    link.click();
                    document.body.removeChild(link);

                    button.textContent = '保存成功!';
                    setTimeout(() => {
                        button.textContent = originalText;
                        button.disabled = false;
                    }, 2000);

                } catch (error) {
                    const buttons = document.querySelector('.save-buttons');
                    buttons.style.visibility = 'visible';
                    button.textContent = '保存失败';
                    setTimeout(() => {
                        button.textContent = originalText;
                        button.disabled = false;
                    }, 2000);
                }
            }

            async function saveAsMultipleImages() {
                const button = event.target;
                const originalText = button.textContent;
                const container = document.querySelector('.container');
                const scale = 1.5;
                const maxHeight = 5000 / scale;

                try {
                    button.textContent = '分析中...';
                    button.disabled = true;

                    // 获取所有可能的分割元素
                    const newsItems = Array.from(container.querySelectorAll('.news-item'));
                    const wordGroups = Array.from(container.querySelectorAll('.word-group'));
                    const newSection = container.querySelector('.new-section');
                    const errorSection = container.querySelector('.error-section');
                    const header = container.querySelector('.header');
                    const footer = container.querySelector('.footer');

                    // 计算元素位置和高度
                    const containerRect = container.getBoundingClientRect();
                    const elements = [];

                    // 添加header作为必须包含的元素
                    elements.push({
                        type: 'header',
                        element: header,
                        top: 0,
                        bottom: header.offsetHeight,
                        height: header.offsetHeight
                    });

                    // 添加错误信息（如果存在）
                    if (errorSection) {
                        const rect = errorSection.getBoundingClientRect();
                        elements.push({
                            type: 'error',
                            element: errorSection,
                            top: rect.top - containerRect.top,
                            bottom: rect.bottom - containerRect.top,
                            height: rect.height
                        });
                    }

                    // 按word-group分组处理news-item
                    wordGroups.forEach(group => {
                        const groupRect = group.getBoundingClientRect();
                        const groupNewsItems = group.querySelectorAll('.news-item');

                        // 添加word-group的header部分
                        const wordHeader = group.querySelector('.word-header');
                        if (wordHeader) {
                            const headerRect = wordHeader.getBoundingClientRect();
                            elements.push({
                                type: 'word-header',
                                element: wordHeader,
                                parent: group,
                                top: groupRect.top - containerRect.top,
                                bottom: headerRect.bottom - containerRect.top,
                                height: headerRect.height
                            });
                        }

                        // 添加每个news-item
                        groupNewsItems.forEach(item => {
                            const rect = item.getBoundingClientRect();
                            elements.push({
                                type: 'news-item',
                                element: item,
                                parent: group,
                                top: rect.top - containerRect.top,
                                bottom: rect.bottom - containerRect.top,
                                height: rect.height
                            });
                        });
                    });

                    // 添加新增新闻部分
                    if (newSection) {
                        const rect = newSection.getBoundingClientRect();
                        elements.push({
                            type: 'new-section',
                            element: newSection,
                            top: rect.top - containerRect.top,
                            bottom: rect.bottom - containerRect.top,
                            height: rect.height
                        });
                    }

                    // 添加footer
                    const footerRect = footer.getBoundingClientRect();
                    elements.push({
                        type: 'footer',
                        element: footer,
                        top: footerRect.top - containerRect.top,
                        bottom: footerRect.bottom - containerRect.top,
                        height: footer.offsetHeight
                    });

                    // 计算分割点
                    const segments = [];
                    let currentSegment = { start: 0, end: 0, height: 0, includeHeader: true };
                    let headerHeight = header.offsetHeight;
                    currentSegment.height = headerHeight;

                    for (let i = 1; i < elements.length; i++) {
                        const element = elements[i];
                        const potentialHeight = element.bottom - currentSegment.start;

                        // 检查是否需要创建新分段
                        if (potentialHeight > maxHeight && currentSegment.height > headerHeight) {
                            // 在前一个元素结束处分割
                            currentSegment.end = elements[i - 1].bottom;
                            segments.push(currentSegment);

                            // 开始新分段
                            currentSegment = {
                                start: currentSegment.end,
                                end: 0,
                                height: element.bottom - currentSegment.end,
                                includeHeader: false
                            };
                        } else {
                            currentSegment.height = potentialHeight;
                            currentSegment.end = element.bottom;
                        }
                    }

                    // 添加最后一个分段
                    if (currentSegment.height > 0) {
                        currentSegment.end = container.offsetHeight;
                        segments.push(currentSegment);
                    }

                    button.textContent = `生成中 (0/${segments.length})...`;

                    // 隐藏保存按钮
                    const buttons = document.querySelector('.save-buttons');
                    buttons.style.visibility = 'hidden';

                    // 为每个分段生成图片
                    const images = [];
                    for (let i = 0; i < segments.length; i++) {
                        const segment = segments[i];
                        button.textContent = `生成中 (${i + 1}/${segments.length})...`;

                        // 创建临时容器用于截图
                        const tempContainer = document.createElement('div');
                        tempContainer.style.cssText = `
                            position: absolute;
                            left: -9999px;
                            top: 0;
                            width: ${container.offsetWidth}px;
                            background: white;
                        `;
                        tempContainer.className = 'container';

                        // 克隆容器内容
                        const clonedContainer = container.cloneNode(true);

                        // 移除克隆内容中的保存按钮
                        const clonedButtons = clonedContainer.querySelector('.save-buttons');
                        if (clonedButtons) {
                            clonedButtons.style.display = 'none';
                        }

                        tempContainer.appendChild(clonedContainer);
                        document.body.appendChild(tempContainer);

                        // 等待DOM更新
                        await new Promise(resolve => setTimeout(resolve, 100));

                        // 使用html2canvas截取特定区域
                        const canvas = await html2canvas(clonedContainer, {
                            backgroundColor: '#ffffff',
                            scale: scale,
                            useCORS: true,
                            allowTaint: false,
                            imageTimeout: 10000,
                            logging: false,
                            width: container.offsetWidth,
                            height: segment.end - segment.start,
                            x: 0,
                            y: segment.start,
                            windowWidth: window.innerWidth,
                            windowHeight: window.innerHeight
                        });

                        images.push(canvas.toDataURL('image/png', 1.0));

                        // 清理临时容器
                        document.body.removeChild(tempContainer);
                    }

                    // 恢复按钮显示
                    buttons.style.visibility = 'visible';

                    // 下载所有图片
                    const now = new Date();
                    const baseFilename = `NewsPilot_热点新闻分析_${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, '0')}${String(now.getDate()).padStart(2, '0')}_${String(now.getHours()).padStart(2, '0')}${String(now.getMinutes()).padStart(2, '0')}`;

                    for (let i = 0; i < images.length; i++) {
                        const link = document.createElement('a');
                        link.download = `${baseFilename}_part${i + 1}.png`;
                        link.href = images[i];
                        document.body.appendChild(link);
                        link.click();
                        document.body.removeChild(link);

                        // 延迟一下避免浏览器阻止多个下载
                        await new Promise(resolve => setTimeout(resolve, 100));
                    }

                    button.textContent = `已保存 ${segments.length} 张图片!`;
                    setTimeout(() => {
                        button.textContent = originalText;
                        button.disabled = false;
                    }, 2000);

                } catch (error) {
                    console.error('分段保存失败:', error);
                    const buttons = document.querySelector('.save-buttons');
                    buttons.style.visibility = 'visible';
                    button.textContent = '保存失败';
                    setTimeout(() => {
                        button.textContent = originalText;
                        button.disabled = false;
                    }, 2000);
                }
            }

            document.addEventListener('DOMContentLoaded', function() {
                window.scrollTo(0, 0);
                initSelectionUI();
            });

            // === Weekly Export: Select & Export ===
            let iaSelectMode = false;

            function initSelectionUI() {
                const items = Array.from(document.querySelectorAll('.rss-item, .news-item'));
                let nextId = 1;

                items.forEach(el => {
                    if (el.dataset.iaSelectId) return;
                    el.dataset.iaSelectId = String(nextId++);
                    el.classList.add('ia-selectable');

                    const pick = document.createElement('label');
                    pick.className = 'ia-pick';
                    pick.innerHTML = '<input type="checkbox" class="ia-pick-checkbox" />选择';

                    const checkbox = pick.querySelector('input');
                    checkbox.addEventListener('change', () => {
                        el.classList.toggle('ia-selected', checkbox.checked);
                        updateSelectionButtons();
                    });

                    el.prepend(pick);
                });

                updateSelectionButtons();
            }

            function getSelectedElementsInOrder() {
                const all = Array.from(document.querySelectorAll('[data-ia-select-id]'));
                return all.filter(el => {
                    const checkbox = el.querySelector('.ia-pick-checkbox');
                    return Boolean(checkbox && checkbox.checked);
                });
            }

            function updateSelectionButtons() {
                const toggleBtn = document.getElementById('iaToggleSelectBtn');
                const clearBtn = document.getElementById('iaClearSelectBtn');
                const exportBtn = document.getElementById('iaExportWeeklyBtn');

                const selectedCount = getSelectedElementsInOrder().length;

                if (toggleBtn) toggleBtn.textContent = iaSelectMode ? '退出选择' : '选择资讯';
                if (clearBtn) clearBtn.disabled = selectedCount === 0;
                if (exportBtn) exportBtn.disabled = selectedCount === 0;
                if (exportBtn) exportBtn.textContent = selectedCount ? `生成周报（${selectedCount}）` : '生成周报';
            }

            function toggleSelectionMode() {
                iaSelectMode = !iaSelectMode;
                document.body.classList.toggle('select-mode', iaSelectMode);
                updateSelectionButtons();
            }

            function clearSelections() {
                const selected = getSelectedElementsInOrder();
                selected.forEach(el => {
                    const checkbox = el.querySelector('.ia-pick-checkbox');
                    if (checkbox) checkbox.checked = false;
                    el.classList.remove('ia-selected');
                });
                updateSelectionButtons();
            }

            function exportWeeklyDigest() {
                const selected = getSelectedElementsInOrder();
                if (selected.length === 0) {
                    alert('请先选择若干条资讯再生成周报。');
                    return;
                }

                const weeklyTitle = '操作系统部一周AI资讯洞察';
                const styleEl = document.querySelector('style');
                const baseCss = styleEl ? styleEl.textContent : '';

                const extraCss = `
                    .header-info { display: none !important; }
                    .rss-section-header, .rss-section-title, .feed-header, .word-header { display: none !important; }
                `;

                const contentWrapper = document.createElement('div');
                selected.forEach(el => {
                    const clone = el.cloneNode(true);
                    clone.classList.remove('ia-selected', 'ia-selectable');
                    clone.querySelectorAll('.ia-pick').forEach(p => p.remove());

                    clone.querySelectorAll('.ai-viewpoint').forEach(v => {
                        const t = (v.textContent || '').trim();
                        if (t && !t.startsWith('OS观点：')) v.textContent = `OS观点：${t}`;
                    });

                    contentWrapper.appendChild(clone);
                });

                const now = new Date();
                const y = now.getFullYear();
                const m = String(now.getMonth() + 1).padStart(2, '0');
                const d = String(now.getDate()).padStart(2, '0');
                const filename = `${weeklyTitle}_${y}${m}${d}.html`;

                const html = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>${weeklyTitle}</title>
  <style>${baseCss}\n${extraCss}</style>
</head>
<body>
  <div class="container">
    <div class="header">
      <div class="header-title">${weeklyTitle}</div>
    </div>
    <div class="content">
      ${contentWrapper.innerHTML}
    </div>
    <div class="footer">
      <div class="footer-content">
        由 <span class="project-name">AIOS</span> 生成
      </div>
    </div>
  </div>
</body>
</html>`;

                const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = filename;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
            }
        </script>
    </body>
    </html>
    """

    return html
