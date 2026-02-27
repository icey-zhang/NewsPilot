# coding=utf-8
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, TypedDict

from InfoAgent.legacy.analyzer import NewsAnalyzer
from langgraph.graph import StateGraph, END
from InfoAgent.utils.url import normalize_rss_url_key

def _collect_rss_urls_from_stats(stats: Optional[List[Dict]]) -> set[str]:
    urls: set[str] = set()
    if not stats:
        return urls
    for stat in stats:
        if not isinstance(stat, dict):
            continue
        titles = stat.get("titles") or []
        if not isinstance(titles, list):
            continue
        for t in titles:
            if not isinstance(t, dict):
                continue
            url = (t.get("url") or "").strip()
            if url:
                urls.add(url)
    return urls

def _load_cached_rss_item_enrich_by_url(*, date: str) -> Dict[str, Dict[str, str]]:
    try:
        from InfoAgent.storage.llm_store import get_latest_llm_run
    except Exception:
        return {}

    run = get_latest_llm_run(output_dir="output", date=date, kind="rss_item_enrich")
    if not run or not isinstance(run.get("payload"), dict):
        return {}
    payload = run["payload"]
    items_payload = payload.get("items") or {}
    if not isinstance(items_payload, dict):
        return {}
    items = items_payload.get("items")
    if not isinstance(items, list):
        return {}
    out: Dict[str, Dict[str, str]] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        url_raw = (it.get("url") or "").strip()
        key = normalize_rss_url_key(url_raw)
        if not key:
            continue
        out[key] = {
            "title": (it.get("title") or "").strip(),
            "summary": (it.get("summary") or "").strip(),
            "viewpoint": (it.get("viewpoint") or "").strip(),
        }
    return out

class InfoAgentState(TypedDict, total=False):
    analyzer: NewsAnalyzer
    include_news: bool
    include_rss: bool
    llm_tasks: List[str]

    results: Dict
    id_to_name: Dict
    failed_ids: List
    rss_items: Optional[List[Dict]]
    rss_new_items: Optional[List[Dict]]

    llm_enrichment: Optional[Dict[str, Any]]
    llm_debug: Optional[Dict[str, Any]]
    summary_html: Optional[str]


def _node_init(state: InfoAgentState) -> InfoAgentState:
    analyzer = NewsAnalyzer()
    analyzer._initialize_and_check_config()
    return {"analyzer": analyzer}


def _node_crawl_news(state: InfoAgentState) -> InfoAgentState:
    analyzer = state["analyzer"]
    if not state.get("include_news", True):
        return {"results": {}, "id_to_name": {}, "failed_ids": []}

    # 无平台时不抓热榜，避免后续 current 模式“读不到新闻”导致失败
    if not analyzer.ctx.platform_ids:
        return {"results": {}, "id_to_name": {}, "failed_ids": []}

    results, id_to_name, failed_ids = analyzer._crawl_data()
    return {"results": results, "id_to_name": id_to_name, "failed_ids": failed_ids}


def _node_crawl_rss(state: InfoAgentState) -> InfoAgentState:
    analyzer = state["analyzer"]
    if not state.get("include_rss", True):
        return {"rss_items": None, "rss_new_items": None}

    rss_items, rss_new_items = analyzer._crawl_rss_data()
    return {"rss_items": rss_items, "rss_new_items": rss_new_items}


def _node_llm_enrich(state: InfoAgentState) -> InfoAgentState:
    analyzer = state["analyzer"]
    tasks = [t.strip().lower() for t in (state.get("llm_tasks") or []) if t and t.strip()]
    if not tasks:
        llm_cfg = analyzer.ctx.config.get("LLM") if hasattr(analyzer, "ctx") else {}
        tasks = list(llm_cfg.get("TASKS") or []) if isinstance(llm_cfg, dict) else []
    llm_cfg = analyzer.ctx.config.get("LLM") if hasattr(analyzer, "ctx") else {}
    enabled = bool(llm_cfg.get("ENABLED", False)) if isinstance(llm_cfg, dict) else False
    base_url = (llm_cfg.get("BASE_URL", "") or "").strip() if isinstance(llm_cfg, dict) else ""
    model = (llm_cfg.get("MODEL", "") or "").strip() if isinstance(llm_cfg, dict) else ""
    api_key_present = bool((llm_cfg.get("API_KEY", "") or "").strip()) if isinstance(llm_cfg, dict) else False

    debug = {
        "enabled": enabled,
        "effective_tasks": tasks,
        "config_base_url_present": bool(base_url),
        "config_model": model,
        "config_api_key_present": api_key_present,
        # "note": "llm_tasks 可能被 Cherry Studio 参数校验拦截；可用 config/config.yaml 的 llm.tasks 作为默认任务。",
    }

    if not enabled:
        debug["skipped_reason"] = "llm.disabled"
        return {"llm_enrichment": None, "llm_debug": debug}
    if not tasks:
        debug["skipped_reason"] = "no_tasks"
        return {"llm_enrichment": None, "llm_debug": debug}

    try:
        from InfoAgent.llm.enrich import enrich_rss_and_persist
    except Exception as e:
        print(f"[LLM] 富化模块不可用，跳过: {e}")
        debug["skipped_reason"] = "enrich_module_unavailable"
        debug["error"] = str(e)
        return {"llm_enrichment": None, "llm_debug": debug}

    only_urls = _collect_rss_urls_from_stats(state.get("rss_items")) | _collect_rss_urls_from_stats(state.get("rss_new_items"))
    enrichment = enrich_rss_and_persist(
        storage_manager=analyzer.storage_manager,
        analyzer=analyzer,
        tasks=tasks,
        only_urls=sorted(only_urls) if only_urls else None,
    )
    if enrichment is None:
        debug["skipped_reason"] = "enrich_returned_none"
    else:
        debug["skipped_reason"] = None
    return {"llm_enrichment": enrichment, "llm_debug": debug}


def _apply_rss_item_enrich(stats: Optional[List[Dict]], by_url: Dict[str, Dict[str, str]]) -> Optional[List[Dict]]:
    if not stats or not by_url:
        return stats
    for stat in stats:
        titles = stat.get("titles") or []
        for t in titles:
            url = (t.get("url") or "").strip()
            if not url:
                continue
            enrich = by_url.get(url) or by_url.get(normalize_rss_url_key(url))
            if not enrich:
                continue
            if enrich.get("title"):
                t["llm_title"] = enrich["title"]
            if enrich.get("summary"):
                t["llm_summary"] = enrich["summary"]
            if enrich.get("viewpoint"):
                t["llm_viewpoint"] = enrich["viewpoint"]
    return stats


def _node_execute(state: InfoAgentState) -> InfoAgentState:
    analyzer = state["analyzer"]
    mode_strategy = analyzer._get_mode_strategy()

    results = state.get("results") or {}
    id_to_name = state.get("id_to_name") or {}
    failed_ids = state.get("failed_ids") or []
    rss_items = state.get("rss_items")
    rss_new_items = state.get("rss_new_items")

    llm_enrichment = state.get("llm_enrichment") or {}
    by_url = llm_enrichment.get("rss_item_enrich_by_url") or {}
    if not by_url:
        # Even if we didn't run enrichment this time, reuse cached LLM results for HTML rendering.
        llm_cfg = analyzer.ctx.config.get("LLM") if hasattr(analyzer, "ctx") else {}
        llm_enabled = bool(llm_cfg.get("ENABLED", False)) if isinstance(llm_cfg, dict) else False
        use_cache = os.environ.get("TREND_LLM_USE_CACHE", "").strip().lower() not in {"0", "false", "no", "off"}
        if llm_enabled and use_cache:
            by_url = _load_cached_rss_item_enrich_by_url(date=analyzer.ctx.format_date())
    rss_items = _apply_rss_item_enrich(rss_items, by_url)
    rss_new_items = _apply_rss_item_enrich(rss_new_items, by_url)

    # RSS-only：当未配置任何热榜平台时，跳过历史 news 读取逻辑，直接生成 RSS 报告并发送通知
    if not analyzer.ctx.platform_ids:
        word_groups, filter_words, global_filters = analyzer.ctx.load_frequency_words()
        report_type = mode_strategy["realtime_report_type"] if mode_strategy.get("should_send_realtime") else mode_strategy.get("summary_report_type", "当日汇总")
        stats, html_file = analyzer._run_analysis_pipeline(
            data_source={},
            mode=analyzer.report_mode,
            title_info={},
            new_titles={},
            word_groups=word_groups,
            filter_words=filter_words,
            id_to_name={},
            failed_ids=[],
            is_daily_summary=True,
            global_filters=global_filters,
            rss_items=rss_items,
            rss_new_items=rss_new_items,
        )
        if html_file:
            print(f"HTML报告已生成: {html_file}")
        analyzer._send_notification_if_needed(
            stats,
            report_type,
            analyzer.report_mode,
            failed_ids=[],
            new_titles={},
            id_to_name={},
            html_file_path=html_file,
            rss_items=rss_items,
            rss_new_items=rss_new_items,
        )
        return {"summary_html": html_file}

    summary_html = analyzer._execute_mode_strategy(
        mode_strategy,
        results,
        id_to_name,
        failed_ids,
        rss_items=rss_items,
        rss_new_items=rss_new_items,
    )
    return {"summary_html": summary_html}


def _node_finalize(state: InfoAgentState) -> InfoAgentState:
    analyzer = state.get("analyzer")
    if analyzer is not None:
        analyzer.ctx.cleanup()
    return {}


def build_and_run(*, include_news: bool, include_rss: bool, llm_tasks: List[str]) -> Dict[str, Any]:
    # try:
    #     from langgraph.graph import StateGraph, END
    # except ImportError as e:
    #     raise ImportError("缺少依赖：请安装 langgraph 才能使用 LangGraph 工作流") from e

    graph = StateGraph(InfoAgentState)
    graph.add_node("init", _node_init)
    graph.add_node("crawl_news", _node_crawl_news)
    graph.add_node("crawl_rss", _node_crawl_rss)
    graph.add_node("llm_enrich", _node_llm_enrich)
    graph.add_node("execute", _node_execute)
    graph.add_node("finalize", _node_finalize)

    graph.set_entry_point("init")
    graph.add_edge("init", "crawl_news")
    graph.add_edge("crawl_news", "crawl_rss")
    graph.add_edge("crawl_rss", "llm_enrich")
    graph.add_edge("llm_enrich", "execute")
    graph.add_edge("execute", "finalize")
    graph.add_edge("finalize", END)

    app = graph.compile()
    final_state = app.invoke(
        {
            "include_news": include_news,
            "include_rss": include_rss,
            "llm_tasks": llm_tasks,
        }
    )

    return {
        "success": True,
        "report_mode": final_state["analyzer"].report_mode if "analyzer" in final_state else None,
        "summary_html": final_state.get("summary_html"),
        "llm_enrichment": final_state.get("llm_enrichment"),
        "llm_debug": final_state.get("llm_debug"),
    }
