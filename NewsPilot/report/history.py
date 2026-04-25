# coding=utf-8
"""
历史更新页面生成器

生成两个静态页面：
- output/history.html：用于在 output 目录下直接访问（相对链接更友好）
- history.html：用于仓库根目录访问（适配 GitHub Pages 等）

页面用于聚合展示 output/YYYY-MM-DD/html/当日汇总.html 的历史列表，并在右侧 iframe 里预览。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


_DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _try_parse_int(value: str) -> Optional[int]:
    m = re.search(r"(\d+)", value)
    return int(m.group(1)) if m else None


def _extract_info_value(html: str, label: str) -> Optional[str]:
    # 兼容 render_html_content 输出：<span class="info-label">X</span> <span class="info-value">Y</span>
    pattern = rf'<span class="info-label">\s*{re.escape(label)}\s*</span>\s*<span class="info-value">\s*([^<]+?)\s*</span>'
    m = re.search(pattern, html, re.S)
    if not m:
        return None
    return m.group(1).strip()


def _read_text_if_exists(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except UnicodeDecodeError:
        # 某些系统可能以其他编码写入；这里不强依赖解析，失败则跳过元信息。
        return None


def _collect_daily_entries(output_dir: Path, daily_filename: str) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []

    if not output_dir.exists():
        return entries

    for child in output_dir.iterdir():
        if not child.is_dir():
            continue
        if not _DATE_DIR_RE.match(child.name):
            continue

        daily_path = child / "html" / daily_filename
        if not daily_path.exists():
            continue

        html = _read_text_if_exists(daily_path) or ""
        total_titles_text = _extract_info_value(html, "新闻总数")
        hot_news_text = _extract_info_value(html, "热点新闻")
        generated_at_text = _extract_info_value(html, "生成时间")
        report_type_text = _extract_info_value(html, "报告类型")

        entries.append(
            {
                "date": child.name,
                "relative_href": f"{child.name}/html/{daily_filename}",
                "meta": {
                    "report_type": report_type_text,
                    "total_titles": _try_parse_int(total_titles_text or ""),
                    "hot_news": _try_parse_int(hot_news_text or ""),
                    "generated_at": generated_at_text,
                },
            }
        )

    # 日期按降序
    entries.sort(key=lambda e: e["date"], reverse=True)
    return entries


def _render_history_html(*, title: str, entries: List[Dict[str, Any]], href_prefix: str) -> str:
    # href_prefix:
    # - output/history.html: ""（日期目录与 history.html 同级）
    # - root history.html: "output/"（日期目录在 output/ 下）
    entries_for_js = []
    for e in entries:
        meta = e.get("meta") or {}
        entries_for_js.append(
            {
                "date": e["date"],
                "href": f"{href_prefix}{e['relative_href']}",
                "reportType": meta.get("report_type") or "",
                "totalTitles": meta.get("total_titles"),
                "hotNews": meta.get("hot_news"),
                "generatedAt": meta.get("generated_at") or "",
            }
        )

    entries_json = json.dumps(entries_for_js, ensure_ascii=False)
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M")

    return f"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{title}</title>
    <style>
      :root {{
        --primary-gradient: linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%);
        --bg: #f3f4f6;
        --card: #ffffff;
        --text: #111827;
        --muted: #6b7280;
        --border: #e5e7eb;
        --shadow: 0 10px 25px -5px rgba(0,0,0,.1), 0 8px 10px -6px rgba(0,0,0,.08);
        --radius: 18px;
      }}
      * {{ box-sizing: border-box; }}
      html, body {{ height: 100%; }}
      body {{
        margin: 0;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
        background: var(--bg);
        color: var(--text);
        -webkit-font-smoothing: antialiased;
        -moz-osx-font-smoothing: grayscale;
      }}
      a {{ color: inherit; text-decoration: none; }}
      .app {{
        height: 100%;
        padding: 18px;
        display: grid;
        grid-template-rows: auto 1fr;
        gap: 14px;
        max-width: 1280px;
        margin: 0 auto;
      }}
      .topbar {{
        background: var(--card);
        border: 1px solid var(--border);
        box-shadow: var(--shadow);
        border-radius: var(--radius);
        padding: 16px 16px;
        display: flex;
        gap: 12px;
        align-items: center;
        justify-content: space-between;
      }}
      .brand {{
        display: flex;
        align-items: baseline;
        gap: 10px;
        min-width: 220px;
      }}
      .brand-title {{
        font-weight: 900;
        letter-spacing: -0.2px;
        font-size: 18px;
      }}
      .brand-sub {{
        font-size: 12px;
        color: var(--muted);
      }}
      .search {{
        flex: 1;
        display: flex;
        gap: 10px;
        align-items: center;
        max-width: 680px;
      }}
      .search input {{
        width: 100%;
        border: 1px solid var(--border);
        background: #fff;
        border-radius: 12px;
        padding: 10px 12px;
        font-size: 14px;
        outline: none;
      }}
      .search input:focus {{
        border-color: #c7d2fe;
        box-shadow: 0 0 0 4px rgba(99,102,241,.15);
      }}
      .btn {{
        border: 1px solid var(--border);
        background: #fff;
        border-radius: 12px;
        padding: 10px 12px;
        font-size: 13px;
        font-weight: 600;
        cursor: pointer;
        transition: transform .08s ease, box-shadow .12s ease, border-color .12s ease;
        white-space: nowrap;
      }}
      .btn:hover {{
        border-color: #c7d2fe;
        box-shadow: 0 6px 16px rgba(15,23,42,.10);
      }}
      .btn:active {{ transform: translateY(1px); }}
      .btn.primary {{
        border: 0;
        color: #fff;
        background: var(--primary-gradient);
      }}
      .layout {{
        min-height: 0;
        display: grid;
        grid-template-columns: 340px 1fr;
        gap: 14px;
      }}
      .panel {{
        background: var(--card);
        border: 1px solid var(--border);
        border-radius: var(--radius);
        box-shadow: var(--shadow);
        min-height: 0;
      }}
      .sidebar {{
        display: grid;
        grid-template-rows: auto 1fr;
        overflow: hidden;
      }}
      .sidebar-head {{
        padding: 14px 14px;
        border-bottom: 1px solid var(--border);
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
      }}
      .sidebar-title {{
        font-size: 13px;
        color: var(--muted);
        font-weight: 700;
      }}
      .count-badge {{
        font-size: 12px;
        color: #374151;
        background: #f3f4f6;
        border: 1px solid var(--border);
        border-radius: 999px;
        padding: 4px 8px;
        font-weight: 700;
      }}
      .list {{
        overflow: auto;
        padding: 8px;
      }}
      .item {{
        border: 1px solid transparent;
        border-radius: 14px;
        padding: 12px 12px;
        display: grid;
        gap: 8px;
        cursor: pointer;
        transition: background .12s ease, border-color .12s ease, transform .08s ease;
      }}
      .item:hover {{
        background: #f8fafc;
        border-color: #e2e8f0;
      }}
      .item.active {{
        background: #eef2ff;
        border-color: #c7d2fe;
      }}
      .item-top {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
      }}
      .date {{
        font-weight: 900;
        letter-spacing: -0.2px;
        font-size: 14px;
      }}
      .rtype {{
        font-size: 12px;
        color: #4f46e5;
        font-weight: 800;
        background: rgba(99,102,241,.12);
        border: 1px solid rgba(99,102,241,.20);
        border-radius: 999px;
        padding: 2px 8px;
        white-space: nowrap;
      }}
      .meta {{
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        color: var(--muted);
        font-size: 12px;
      }}
      .pill {{
        background: #f9fafb;
        border: 1px solid var(--border);
        border-radius: 999px;
        padding: 2px 8px;
        font-weight: 700;
      }}
      .viewer {{
        display: grid;
        grid-template-rows: auto 1fr;
        overflow: hidden;
      }}
      .viewer-head {{
        padding: 14px 14px;
        border-bottom: 1px solid var(--border);
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
      }}
      .viewer-title {{
        display: flex;
        flex-direction: column;
        gap: 2px;
        min-width: 0;
      }}
      .viewer-title .h {{
        font-weight: 900;
        letter-spacing: -0.2px;
        font-size: 14px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }}
      .viewer-title .s {{
        font-size: 12px;
        color: var(--muted);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }}
      .viewer-actions {{
        display: flex;
        gap: 10px;
        align-items: center;
      }}
      iframe {{
        width: 100%;
        height: 100%;
        border: 0;
        background: #fff;
      }}
      .empty {{
        padding: 24px;
        color: var(--muted);
        font-size: 14px;
      }}
      @media (max-width: 980px) {{
        .layout {{ grid-template-columns: 1fr; }}
        .brand {{ min-width: 0; }}
      }}
    </style>
  </head>
  <body>
    <div class="app">
      <div class="topbar">
        <div class="brand">
          <div class="brand-title">NewsPilot 历史更新</div>
          <div class="brand-sub">生成于 {now_text}</div>
        </div>
        <div class="search">
          <input id="q" placeholder="搜索日期（例如 2026-01-09）或关键词（例如 当日汇总）" />
          <button class="btn" id="clearBtn" type="button">清空</button>
        </div>
        <button class="btn primary" id="openLatestBtn" type="button">打开最新</button>
      </div>

      <div class="layout">
        <div class="panel sidebar">
          <div class="sidebar-head">
            <div class="sidebar-title">历史列表</div>
            <div class="count-badge" id="countBadge">0</div>
          </div>
          <div class="list" id="list"></div>
        </div>

        <div class="panel viewer">
          <div class="viewer-head">
            <div class="viewer-title">
              <div class="h" id="viewerH">选择一天查看</div>
              <div class="s" id="viewerS">点击左侧日期后，在右侧预览当日汇总</div>
            </div>
            <div class="viewer-actions">
              <a class="btn" id="openNewTab" href="#" target="_blank" rel="noreferrer">新标签打开</a>
            </div>
          </div>
          <div style="min-height:0;">
            <iframe id="frame" title="Daily Summary"></iframe>
          </div>
        </div>
      </div>
    </div>

    <script>
      const ENTRIES = {entries_json};

      const els = {{
        q: document.getElementById("q"),
        clearBtn: document.getElementById("clearBtn"),
        openLatestBtn: document.getElementById("openLatestBtn"),
        countBadge: document.getElementById("countBadge"),
        list: document.getElementById("list"),
        frame: document.getElementById("frame"),
        viewerH: document.getElementById("viewerH"),
        viewerS: document.getElementById("viewerS"),
        openNewTab: document.getElementById("openNewTab"),
      }};

      function normalizeText(s) {{
        return (s || "").toString().toLowerCase().trim();
      }}

      function itemSearchText(e) {{
        return normalizeText([
          e.date,
          e.reportType,
          e.generatedAt,
          (e.totalTitles == null ? "" : `新闻总数 ${{e.totalTitles}}`),
          (e.hotNews == null ? "" : `热点新闻 ${{e.hotNews}}`),
        ].join(" "));
      }}

      function formatMeta(e) {{
        const parts = [];
        if (e.totalTitles != null) parts.push(`新闻总数 ${{e.totalTitles}}`);
        if (e.hotNews != null) parts.push(`热点新闻 ${{e.hotNews}}`);
        if (e.generatedAt) parts.push(`生成时间 ${{e.generatedAt}}`);
        return parts;
      }}

      function renderList(filterText) {{
        const ft = normalizeText(filterText);
        const filtered = ft
          ? ENTRIES.filter(e => itemSearchText(e).includes(ft))
          : ENTRIES.slice();

        els.countBadge.textContent = filtered.length;
        els.list.innerHTML = "";

        if (filtered.length === 0) {{
          const d = document.createElement("div");
          d.className = "empty";
          d.textContent = "没有匹配的日期。";
          els.list.appendChild(d);
          return;
        }}

        const current = (location.hash || "").replace(/^#/, "");

        filtered.forEach((e, idx) => {{
          const item = document.createElement("div");
          item.className = "item";
          item.dataset.date = e.date;
          item.dataset.href = e.href;
          item.tabIndex = 0;

          const top = document.createElement("div");
          top.className = "item-top";

          const date = document.createElement("div");
          date.className = "date";
          date.textContent = e.date;

          const rtype = document.createElement("div");
          rtype.className = "rtype";
          rtype.textContent = e.reportType || "当日汇总";

          top.appendChild(date);
          top.appendChild(rtype);

          const meta = document.createElement("div");
          meta.className = "meta";
          formatMeta(e).forEach(t => {{
            const p = document.createElement("span");
            p.className = "pill";
            p.textContent = t;
            meta.appendChild(p);
          }});

          item.appendChild(top);
          item.appendChild(meta);

          function onPick() {{
            pick(e.date, e.href);
          }}
          item.addEventListener("click", onPick);
          item.addEventListener("keydown", (ev) => {{
            if (ev.key === "Enter" || ev.key === " ") {{
              ev.preventDefault();
              onPick();
            }}
          }});

          if (current && e.date === current) {{
            item.classList.add("active");
          }}
          els.list.appendChild(item);
        }});
      }}

      function setActive(date) {{
        Array.from(els.list.querySelectorAll(".item")).forEach(el => {{
          el.classList.toggle("active", el.dataset.date === date);
        }});
      }}

      function pick(date, href) {{
        setActive(date);
        els.frame.src = href;
        els.viewerH.textContent = date;
        els.viewerS.textContent = href;
        els.openNewTab.href = href;
        location.hash = `#${{date}}`;
      }}

      function openLatest() {{
        if (ENTRIES.length === 0) return;
        pick(ENTRIES[0].date, ENTRIES[0].href);
      }}

      function init() {{
        els.countBadge.textContent = ENTRIES.length;
        renderList("");

        const hashDate = (location.hash || "").replace(/^#/, "");
        if (hashDate) {{
          const found = ENTRIES.find(e => e.date === hashDate);
          if (found) {{
            pick(found.date, found.href);
            return;
          }}
        }}
        openLatest();
      }}

      els.q.addEventListener("input", () => renderList(els.q.value));
      els.clearBtn.addEventListener("click", () => {{
        els.q.value = "";
        renderList("");
        els.q.focus();
      }});
      els.openLatestBtn.addEventListener("click", openLatest);

      init();
    </script>
  </body>
</html>
"""


def generate_history_pages(
    *,
    output_dir: str = "output",
    daily_filename: str = "当日汇总.html",
    write_root_page: bool = True,
    write_output_page: bool = True,
) -> Dict[str, str]:
    """
    生成历史更新页面。

    Returns:
        Dict[str, str]: 已生成文件路径映射，如 {"root": "history.html", "output": "output/history.html"}
    """
    out_dir = Path(output_dir)
    entries = _collect_daily_entries(out_dir, daily_filename)

    results: Dict[str, str] = {}

    if write_output_page:
        output_history_path = out_dir / "history.html"
        output_history_path.parent.mkdir(parents=True, exist_ok=True)
        output_history_path.write_text(
            _render_history_html(
                title="NewsPilot 历史更新（output）",
                entries=entries,
                href_prefix="",
            ),
            encoding="utf-8",
        )
        results["output"] = str(output_history_path)

    if write_root_page:
        root_history_path = Path("history.html")
        root_history_path.write_text(
            _render_history_html(
                title="NewsPilot 历史更新",
                entries=entries,
                href_prefix=f"{output_dir.rstrip('/')}/" if output_dir else "output/",
            ),
            encoding="utf-8",
        )
        results["root"] = str(root_history_path)

    return results
