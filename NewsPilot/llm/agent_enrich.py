# coding=utf-8
"""
Agent-native enrichment support.

This module provides CLI commands for:
1. Fetching RSS items for Agent to enrich
2. Saving Agent's enrichment results to database
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def _load_config():
    """Load config from config/config.yaml"""
    from NewsPilot.core import load_config
    return load_config()


def _get_storage_manager():
    """Get storage manager instance"""
    from NewsPilot.storage.manager import StorageManager
    return StorageManager()


def get_rss_items_for_agent(date: Optional[str] = None) -> Dict[str, Any]:
    """
    Fetch RSS items from storage for Agent to enrich.

    Returns:
        {
            "date": str,
            "items": [{"url": ..., "title": ..., "feed_name": ..., "published_at": ...}, ...],
            "enrichment_prompt": str  # Instructions for Agent
        }
    """
    from NewsPilot.utils.time import get_configured_time, is_within_days, DEFAULT_TIMEZONE

    storage = _get_storage_manager()
    target_date = date or datetime.now().strftime("%Y-%m-%d")

    rss_data = storage.get_latest_rss_data(target_date) or storage.get_rss_data(target_date)
    if not rss_data:
        return {"date": target_date, "items": [], "error": "No RSS data found"}

    # Read max_age_days from config (RSS.freshness_filter.max_age_days, default 7)
    try:
        cfg = _load_config()
        rss_cfg = cfg.get("RSS") or cfg.get("rss") or {}
        freshness_cfg = rss_cfg.get("freshness_filter") or {}
        max_age_days: int = int(freshness_cfg.get("max_age_days") or 7)
        tz_name: str = (cfg.get("app") or {}).get("timezone") or DEFAULT_TIMEZONE
    except Exception:
        max_age_days = 7
        tz_name = DEFAULT_TIMEZONE

    # Convert RSSData to simple list, applying published_at freshness filter
    items: List[Dict[str, Any]] = []
    skipped = 0
    for feed_id, rss_list in rss_data.items.items():
        feed_name = rss_data.id_to_name.get(feed_id, feed_id)
        for item in rss_list:
            # Skip items older than max_age_days (same logic as push-stage filter)
            if max_age_days > 0 and item.published_at:
                if not is_within_days(item.published_at, max_age_days, tz_name):
                    skipped += 1
                    continue
            items.append({
                "url": item.url,
                "title": item.title,
                "feed_name": feed_name,
                "feed_id": feed_id,
                "published_at": item.published_at,
            })

    if skipped:
        print(f"[agent_enrich] 新鲜度过滤：跳过 {skipped} 篇超过 {max_age_days} 天的旧文章")

    # Sort by published_at descending
    items = sorted(items, key=lambda x: x.get("published_at", ""), reverse=True)

    enrichment_prompt = """
对每条 RSS 文章生成：
- title: 一句话概括真正内容（避免标题党）
- summary: 2-3句话，发生了什么、解决了什么问题、核心做法
- viewpoint: 2-3句话，判断与趋势（可含不确定性声明）

写作要求：
- 总字数尽量短，能删就删
- 不复述细节、不堆例子
- 观点是抽象一层后的判断，不是原文改写
- 避免"很重要/具有里程碑意义/未来可期"等空话
- 语气冷静、专业

输出严格 JSON 格式：
{"items": [{"url": "...", "title": "...", "summary": "...", "viewpoint": "..."}]}
"""

    return {
        "date": target_date,
        "items": items,
        "enrichment_prompt": enrichment_prompt,
        "total_count": len(items),
    }


def save_agent_enrichment(
    *,
    date: str,
    enrichment_json: Dict[str, Any],
    model: str = "agent-native",
) -> str:
    """
    Save Agent's enrichment results to database.

    Each call merges the new items with the latest previous run for
    (date, kind="rss_item_enrich") so the routine can save items
    one-at-a-time without losing earlier saves. Merge key is `url`;
    the latest call wins for any given url.

    Args:
        date: Date string (YYYY-MM-DD)
        enrichment_json: {"items": [{"url": ..., "title": ..., "summary": ..., "viewpoint": ...}]}
        model: Model identifier (default "agent-native")

    Returns:
        Path to saved database file
    """
    from NewsPilot.storage.llm_store import get_latest_llm_run, save_llm_run

    def _normalize(items_list):
        out = []
        if isinstance(items_list, list):
            for it in items_list:
                if isinstance(it, dict):
                    out.append({
                        "url": (it.get("url") or "").strip(),
                        "title": (it.get("title") or "").strip(),
                        "summary": (it.get("summary") or "").strip(),
                        "viewpoint": (it.get("viewpoint") or "").strip(),
                    })
        return out

    new_items = _normalize(enrichment_json.get("items") or [])

    # Merge with latest previous run for this date+kind, dedup by url.
    prev = get_latest_llm_run(output_dir="output", date=date, kind="rss_item_enrich")
    merged_by_url: Dict[str, Dict[str, Any]] = {}
    if prev:
        prev_items = prev.get("payload", {}).get("items", {}).get("items", [])
        for it in _normalize(prev_items):
            if it["url"]:
                merged_by_url[it["url"]] = it
    for it in new_items:
        if it["url"]:
            merged_by_url[it["url"]] = it

    merged_items = list(merged_by_url.values())

    db_path = save_llm_run(
        output_dir="output",
        date=date,
        kind="rss_item_enrich",
        model=model,
        payload={
            "date": date,
            "mode": "agent",
            "prompt_version": 2,
            "items": {"items": merged_items},
        },
    )
    return db_path


def _cmd_fetch(args):
    """CLI: Fetch RSS items for Agent"""
    result = get_rss_items_for_agent(date=args.date)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _cmd_save(args):
    """CLI: Save Agent enrichment results"""
    if args.input_file:
        with open(args.input_file, "r", encoding="utf-8") as f:
            enrichment_json = json.load(f)
    elif args.json:
        enrichment_json = json.loads(args.json)
    else:
        print("Error: --input-file or --json required")
        sys.exit(1)

    db_path = save_agent_enrichment(
        date=args.date,
        enrichment_json=enrichment_json,
        model=args.model or "agent-native",
    )
    print(f"Saved to: {db_path}")


def main():
    """CLI entry point"""
    parser = argparse.ArgumentParser(
        prog="NewsPilot llm.agent_enrich",
        description="Agent-native enrichment support",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # fetch: Get RSS items for Agent to enrich
    fetch_parser = subparsers.add_parser("fetch", help="Fetch RSS items for enrichment")
    fetch_parser.add_argument("--date", default="", help="Date (YYYY-MM-DD, default today)")
    fetch_parser.set_defaults(func=_cmd_fetch)

    # save: Save Agent's enrichment results
    save_parser = subparsers.add_parser("save", help="Save enrichment results")
    save_parser.add_argument("--date", required=True, help="Date (YYYY-MM-DD)")
    save_parser.add_argument("--input-file", default="", help="JSON file path")
    save_parser.add_argument("--json", default="", help="JSON string")
    save_parser.add_argument("--model", default="agent-native", help="Model identifier")
    save_parser.set_defaults(func=_cmd_save)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()