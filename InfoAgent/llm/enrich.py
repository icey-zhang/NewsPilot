# coding=utf-8
from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

from InfoAgent.llm.openai_compat import OpenAICompatClient
from InfoAgent.llm.prompt import (
    RSS_ITEM_ENRICH_PROMPT_VERSION,
    build_rss_item_enrich_prompt,
    build_rss_items_enrich_prompt,
)
from InfoAgent.llm.fulltext import FullTextConfig, fetch_article_text
from InfoAgent.storage.llm_store import get_latest_llm_run, save_llm_run
from InfoAgent.utils.url import normalize_rss_url_key


def _lookup_cache_by_url(
    cache_by_url: Dict[str, Dict[str, str]], url: str
) -> Optional[Dict[str, str]]:
    if not cache_by_url or not url:
        return None
    url = url.strip()
    if not url:
        return None
    key = normalize_rss_url_key(url)
    return cache_by_url.get(key) or cache_by_url.get(url)


def _cache_has_any_field(cached: Optional[Dict[str, str]]) -> bool:
    if not cached:
        return False
    return bool(
        (cached.get("summary") or "").strip()
        or (cached.get("viewpoint") or "").strip()
        or (cached.get("title") or "").strip()
    )

def _cache_has_summary_and_viewpoint(cached: Optional[Dict[str, str]]) -> bool:
    if not cached:
        return False
    return bool((cached.get("summary") or "").strip()) and bool((cached.get("viewpoint") or "").strip())


def enrich_rss_and_persist(
    *,
    storage_manager,
    analyzer,
    tasks: List[str],
    only_urls: Optional[Sequence[str]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Enrich RSS content with optional LLM tasks and persist results to `output/llm/{date}.db`.

    Currently focuses on RSS items (news enrichment can be added later).
    """
    # Load latest RSS data from storage (already persisted by crawler)
    date = analyzer.ctx.format_date()
    rss_data = storage_manager.get_latest_rss_data(date) or storage_manager.get_rss_data(date)
    if not rss_data:
        print("[LLM] 未找到 RSS 数据，跳过富化")
        return None

    rss_items = analyzer._convert_rss_items_to_list(rss_data.items, rss_data.id_to_name)
    if not rss_items:
        print("[LLM] RSS 条目为空，跳过富化")
        return None

    only_urls_set: Optional[Set[str]] = None
    if only_urls:
        only_urls_set = {u.strip() for u in only_urls if u and str(u).strip()}
        if only_urls_set:
            rss_items = [it for it in rss_items if (it.get("url") or "").strip() in only_urls_set]
            if not rss_items:
                print("[LLM] RSS 匹配条目为空（已按展示内容过滤），跳过富化")
                return None

    llm_cfg = (analyzer.ctx.config.get("LLM") or {}) if hasattr(analyzer, "ctx") else {}
    llm_enabled = bool(llm_cfg.get("ENABLED", False))
    if not llm_enabled:
        print("[LLM] config.llm.enabled=false，跳过富化")
        return None

    # Keep newest items for cost control (default 50; can be overridden via llm.item_enrich.max_items / TREND_LLM_ITEM_MAX_ITEMS)
    rss_items = sorted(rss_items, key=lambda x: x.get("published_at", ""), reverse=True)
    rss_items = _dedupe_items_by_url(rss_items)
    item_cfg_raw = llm_cfg.get("ITEM_ENRICH") if isinstance(llm_cfg, dict) else {}
    try:
        configured_max_items = int(
            os.environ.get("TREND_LLM_ITEM_MAX_ITEMS")
            or (item_cfg_raw.get("MAX_ITEMS", 0) if isinstance(item_cfg_raw, dict) else 0)
            or 0
        )
    except Exception:
        configured_max_items = 0
    # When aligning with displayed RSS URLs, do not truncate by the default 50 cap.
    keep_max_items = configured_max_items if configured_max_items > 0 else (len(rss_items) if only_urls_set else 50)
    rss_items = rss_items[:keep_max_items]

    base_url = os.environ.get("TREND_LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or llm_cfg.get("BASE_URL")
    api_key = os.environ.get("TREND_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY") or llm_cfg.get("API_KEY")
    model = os.environ.get("TREND_LLM_MODEL") or llm_cfg.get("MODEL")
    timeout = int(os.environ.get("TREND_LLM_TIMEOUT") or llm_cfg.get("TIMEOUT") or 90)

    try:
        client = OpenAICompatClient(
            base_url=base_url, api_key=api_key, model=model, timeout=timeout
        )
    except Exception as e:
        print(f"[LLM] 未配置 LLM（OpenAI-compatible），跳过: {e}")
        return None

    wants_summary = "summary" in tasks
    wants_classify = "classify" in tasks or "classification" in tasks
    wants_cluster = "cluster" in tasks or "clustering" in tasks
    wants_item_enrich = (
        "item_enrich" in tasks
        or "items" in tasks
        or "viewpoint" in tasks
        or "per_item" in tasks
    )
    # 兼容 MCP 客户端/助手可能只允许 summary/classify/cluster：
    # 让 summary 同时触发逐条“总结/观点”富化，以便在报告中展示。
    if wants_summary:
        wants_item_enrich = True

    result: Dict[str, Any] = {"date": date, "model": client.model}
    debug = os.environ.get("TREND_LLM_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
    if debug:
        print(f"[LLM][DEBUG] tasks={tasks} wants_item_enrich={wants_item_enrich} wants_summary={wants_summary} wants_classify={wants_classify} wants_cluster={wants_cluster}")

    # Fulltext settings (optional)
    ft_cfg_raw = llm_cfg.get("FULLTEXT") if isinstance(llm_cfg, dict) else {}
    fulltext_enabled = bool(ft_cfg_raw.get("ENABLED", False)) if isinstance(ft_cfg_raw, dict) else False
    if os.environ.get("TREND_LLM_FULLTEXT", "").strip().lower() in {"1", "true", "yes", "on"}:
        fulltext_enabled = True

    ft_cfg = FullTextConfig(
        enabled=fulltext_enabled,
        timeout=int(ft_cfg_raw.get("TIMEOUT", 15) or 15) if isinstance(ft_cfg_raw, dict) else 15,
        max_bytes=int(ft_cfg_raw.get("MAX_BYTES", 1200000) or 1200000) if isinstance(ft_cfg_raw, dict) else 1200000,
        max_chars=int(ft_cfg_raw.get("MAX_CHARS", 6000) or 6000) if isinstance(ft_cfg_raw, dict) else 6000,
        min_paragraph_chars=int(ft_cfg_raw.get("MIN_PARAGRAPH_CHARS", 60) or 60) if isinstance(ft_cfg_raw, dict) else 60,
        request_interval_ms=int(ft_cfg_raw.get("REQUEST_INTERVAL_MS", 200) or 200) if isinstance(ft_cfg_raw, dict) else 200,
        use_proxy=bool(ft_cfg_raw.get("USE_PROXY", False)) if isinstance(ft_cfg_raw, dict) else False,
        proxy_url=(ft_cfg_raw.get("PROXY_URL", "") or "").strip() if isinstance(ft_cfg_raw, dict) else "",
    )
    max_fulltext_items = int(ft_cfg_raw.get("MAX_ITEMS", 10) or 10) if isinstance(ft_cfg_raw, dict) else 10

    if wants_summary or wants_classify or wants_cluster:
        system = (
            "你是一个中文资讯分析助手。"
            "你会对给定的 RSS 标题列表做摘要、分类和聚类，并严格输出 JSON。"
            "不要输出任何额外文字。"
        )

        user = _build_rss_prompt(rss_items=rss_items, tasks=tasks)
        schema_hint: Dict[str, Any] = {
            "summary": "string (中文摘要，<=120字)",
            "topics": [{"name": "string", "count": "int"}],
            "clusters": [{"label": "string", "items": [{"title": "string", "url": "string"}]}],
        }

        resp = client.chat_json(
            system=system, user=user, json_schema_hint=schema_hint, temperature=0.2
        )
        if debug:
            _debug_print_response("rss_enrich", resp)
        db_path = save_llm_run(
            output_dir="output",
            date=date,
            kind="rss_enrich",
            model=resp.get("model", client.model),
            payload={
                "tasks": tasks,
                "date": date,
                "item_count": len(rss_items),
                "result": resp.get("json"),
                "raw": resp.get("raw"),
            },
        )
        print(f"[LLM] 富化结果已保存: {db_path}")
        result["rss_enrich_db_path"] = db_path
        result["rss_enrich"] = resp.get("json")

    if wants_item_enrich:
        include_content = ft_cfg.enabled
        items_for_prompt = rss_items

        # Cache: reuse latest per-URL enrichment from output/llm/{date}.db
        use_cache = os.environ.get("TREND_LLM_USE_CACHE", "").strip().lower() not in {"0", "false", "no", "off"}
        cache_by_url: Dict[str, Dict[str, str]] = {}
        cache_meta: Dict[str, Any] = {"enabled": use_cache, "hit": 0, "miss": 0, "skipped_due_to_version": 0}
        if use_cache:
            cached_run = get_latest_llm_run(output_dir="output", date=date, kind="rss_item_enrich")
            if cached_run and isinstance(cached_run.get("payload"), dict):
                payload = cached_run["payload"]
                cached_version = payload.get("prompt_version")
                if cached_version is None or int(cached_version) == int(RSS_ITEM_ENRICH_PROMPT_VERSION):
                    items_payload = payload.get("items") or {}
                    if isinstance(items_payload, dict):
                        cache_by_url = _map_items_by_url(_ensure_items_payload(items_payload))
                else:
                    cache_meta["skipped_due_to_version"] = 1

        item_cfg_raw = llm_cfg.get("ITEM_ENRICH") if isinstance(llm_cfg, dict) else {}
        item_mode = (str(item_cfg_raw.get("MODE", "bulk")) if isinstance(item_cfg_raw, dict) else "bulk").strip().lower()
        item_mode = (os.environ.get("TREND_LLM_ITEM_MODE") or item_mode).strip().lower()
        if item_mode not in {"bulk", "single"}:
            item_mode = "bulk"
        item_max_items = int(os.environ.get("TREND_LLM_ITEM_MAX_ITEMS") or (item_cfg_raw.get("MAX_ITEMS", 0) if isinstance(item_cfg_raw, dict) else 0) or 0)
        llm_interval_ms = int(os.environ.get("TREND_LLM_ITEM_REQUEST_INTERVAL_MS") or (item_cfg_raw.get("REQUEST_INTERVAL_MS", 0) if isinstance(item_cfg_raw, dict) else 0) or 0)
        min_interval_s = 0.0
        if llm_interval_ms > 0:
            min_interval_s = max(min_interval_s, llm_interval_ms / 1000.0)

        if include_content:
            if debug:
                print(f"[LLM][DEBUG] fulltext enabled, fetching up to {max_fulltext_items} uncached articles")
            enriched: List[Dict[str, Any]] = []
            with_content = 0
            attempted = 0
            for it in rss_items:
                url = (it.get("url") or "").strip()
                cached = _lookup_cache_by_url(cache_by_url, url) if url else None
                if url and _cache_has_summary_and_viewpoint(cached):
                    enriched.append(it)
                    continue

                it2 = dict(it)
                did_fetch = False
                if url and attempted < max_fulltext_items:
                    attempted += 1
                    did_fetch = True
                    content = fetch_article_text(url, ft_cfg)
                else:
                    content = None
                if content and len(content) >= 120:
                    it2["content"] = content
                    with_content += 1
                    if debug:
                        preview = content[:200].replace("\n", " ").strip()
                        print(f"[LLM][DEBUG] fulltext ok chars={len(content)} url={url}")
                        print(f"[LLM][DEBUG] fulltext preview: {preview}")
                elif debug:
                    if did_fetch:
                        print(f"[LLM][DEBUG] fulltext failed url={url}")
                enriched.append(it2)
            items_for_prompt = enriched
            if debug:
                print(f"[LLM][DEBUG] fulltext fetched: {with_content}/{attempted}")

        if item_max_items > 0:
            items_for_prompt = items_for_prompt[:item_max_items]

        if debug:
            print(
                f"[LLM][DEBUG] rss_item_enrich mode={item_mode} items={len(items_for_prompt)} "
                f"include_content={include_content} throttle_interval_s={min_interval_s:.2f}"
            )

        debug_prompt = os.environ.get("TREND_LLM_DEBUG_PROMPT", "").strip().lower() in {"1", "true", "yes", "on"}
        debug_no_truncate = os.environ.get("TREND_LLM_DEBUG_NO_TRUNCATE", "").strip().lower() in {"1", "true", "yes", "on"}
        debug_dump = os.environ.get("TREND_LLM_DEBUG_DUMP", "").strip().lower() in {"1", "true", "yes", "on"}
        debug_each = os.environ.get("TREND_LLM_DEBUG_EACH", "").strip().lower() in {"1", "true", "yes", "on"}
        debug_item_max = int(os.environ.get("TREND_LLM_DEBUG_ITEM_MAX") or 3)
        debug_dump_max = int(os.environ.get("TREND_LLM_DEBUG_DUMP_MAX") or 3)

        items_payload: Dict[str, Any]
        mapped: Dict[str, Dict[str, str]]

        if item_mode == "bulk":
            # If cache is available, only send missing items to the model and merge with cached outputs.
            cached_out = []
            missing_items = items_for_prompt
            if cache_by_url:
                missing_items = []
                for it in items_for_prompt:
                    url = (it.get("url") or "").strip()
                    cached = _lookup_cache_by_url(cache_by_url, url) if url else None
                    if _cache_has_summary_and_viewpoint(cached):
                        cache_meta["hit"] += 1
                        cached_out.append(
                            {
                                "url": url,
                                "title": (cached.get("title") or "").strip(),
                                "summary": (cached.get("summary") or "").strip(),
                                "viewpoint": (cached.get("viewpoint") or "").strip(),
                            }
                        )
                    else:
                        cache_meta["miss"] += 1
                        missing_items.append(it)

            if not missing_items:
                items_payload = {"items": cached_out}
                mapped = _map_items_by_url(items_payload)
            else:
                system, user, schema_hint = build_rss_items_enrich_prompt(
                    items=missing_items, include_content=include_content, language="zh"
                )

                if debug and debug_prompt:
                    marker_count = user.count('content="""')
                    preview = user if debug_no_truncate else (user if len(user) <= 1500 else user[:1500] + "\n...<truncated>...")
                    print(f"[LLM][DEBUG] prompt_len={len(user)} content_blocks={marker_count}")
                    print(f"[LLM][DEBUG] prompt_preview:\n{preview}")
                if debug and debug_dump:
                    _debug_dump_text(tag="rss_item_enrich_prompt", text=user)

                try:
                    resp = client.chat_json(
                        system=system, user=user, json_schema_hint=schema_hint, temperature=0.2
                    )
                except Exception as e:
                    # bulk can easily timeout due to prompt size; fallback to per-item to improve robustness
                    print(f"[LLM] rss_item_enrich bulk 调用失败，切换为 single：{e}")
                    item_mode = "single"
                    resp = {}

                if item_mode == "bulk":
                    if debug:
                        _debug_print_response("rss_item_enrich", resp)
                    new_payload = _ensure_items_payload(resp.get("json") or {})
                    merged = _merge_items_lists(cached_out, new_payload.get("items") or [])
                    items_payload = {"items": merged}
                    mapped = _map_items_by_url(items_payload)
                else:
                    items_payload = {"items": cached_out}
                    mapped = _map_items_by_url(items_payload)

        if item_mode == "single":
            out_items: List[Dict[str, Any]] = []
            last_call_start: Optional[float] = None
            for idx, it in enumerate(items_for_prompt):
                url = (it.get("url") or "").strip()
                if not url:
                    continue

                cached = _lookup_cache_by_url(cache_by_url, url) if cache_by_url else None
                if _cache_has_summary_and_viewpoint(cached):
                    cache_meta["hit"] += 1
                    out_items.append(
                        {
                            "url": url,
                            "title": (cached.get("title") or "").strip(),
                            "summary": (cached.get("summary") or "").strip(),
                            "viewpoint": (cached.get("viewpoint") or "").strip(),
                        }
                    )
                    if debug:
                        print(f"[LLM][DEBUG] cache hit idx={idx} url={url}")
                    continue
                if debug and _cache_has_any_field(cached):
                    missing_fields = []
                    if not (cached.get("summary") or "").strip():
                        missing_fields.append("summary")
                    if not (cached.get("viewpoint") or "").strip():
                        missing_fields.append("viewpoint")
                    print(f"[LLM][DEBUG] cache partial hit idx={idx} url={url} missing={','.join(missing_fields) or 'unknown'}")
                cache_meta["miss"] += 1

                system, user, schema_hint = build_rss_item_enrich_prompt(
                    item=it, include_content=include_content, language="zh"
                )
                if debug and debug_prompt and (debug_each or idx < debug_item_max):
                    marker_count = user.count('content="""')
                    preview = user if debug_no_truncate else (user if len(user) <= 1500 else user[:1500] + "\n...<truncated>...")
                    print(f"[LLM][DEBUG] single_prompt idx={idx} prompt_len={len(user)} content_blocks={marker_count} url={url}")
                    print(f"[LLM][DEBUG] prompt_preview:\n{preview}")
                if debug and debug_dump and (debug_each or idx < debug_dump_max):
                    _debug_dump_text(tag=f"rss_item_enrich_prompt_{idx}", text=user)

                if min_interval_s > 0 and last_call_start is not None:
                    now = time.monotonic()
                    sleep_for = (last_call_start + min_interval_s) - now
                    if sleep_for > 0:
                        if debug:
                            print(f"[LLM][DEBUG] throttle sleep {sleep_for:.2f}s before idx={idx}")
                        time.sleep(sleep_for)
                last_call_start = time.monotonic()

                call_started = time.monotonic()
                if debug:
                    print(f"[LLM][DEBUG] calling llm idx={idx} url={url}")
                try:
                    resp = None
                    for attempt in range(1, 4):
                        try:
                            resp = client.chat_json(
                                system=system, user=user, json_schema_hint=schema_hint, temperature=0.2
                            )
                            break
                        except Exception as e:
                            elapsed = time.monotonic() - call_started
                            if attempt < 3:
                                print(
                                    f"[LLM] single 调用失败 idx={idx} url={url} elapsed_s={elapsed:.2f}，重试 {attempt}/3：{e}"
                                )
                                time.sleep(1.0 * attempt)
                                continue
                            print(f"[LLM] single 调用失败 idx={idx} url={url} elapsed_s={elapsed:.2f}: {e}")
                            resp = None
                except Exception as e:
                    elapsed = time.monotonic() - call_started
                    print(f"[LLM] single 调用失败 idx={idx} url={url} elapsed_s={elapsed:.2f}: {e}")
                    continue
                if debug:
                    elapsed = time.monotonic() - call_started
                    print(f"[LLM][DEBUG] llm done idx={idx} elapsed_s={elapsed:.2f} url={url}")
                if resp is None:
                    continue

                if debug and (debug_each or idx < debug_item_max):
                    _debug_print_response("rss_item_enrich", resp)

                payload = _coerce_single_item_payload(resp.get("json"), url)
                if not payload:
                    if debug:
                        print(f"[LLM][DEBUG] single parsed_json missing/invalid idx={idx} url={url}")
                    continue

                out_items.append(
                    {
                        "url": url,
                        "title": (payload.get("title") or "").strip(),
                        "summary": (payload.get("summary") or "").strip(),
                        "viewpoint": (payload.get("viewpoint") or "").strip(),
                    }
                )

            items_payload = {"items": out_items}
            mapped = _map_items_by_url(items_payload)

        db_path = save_llm_run(
            output_dir="output",
            date=date,
            kind="rss_item_enrich",
            model=client.model,
            payload={
                "tasks": tasks,
                "date": date,
                "mode": item_mode,
                "prompt_version": RSS_ITEM_ENRICH_PROMPT_VERSION,
                "cache": cache_meta,
                "item_count": len(rss_items),
                "items": _ensure_items_payload(items_payload),
                # raw responses can be large; keep only when explicitly dumping/debugging via files
            },
        )
        print(f"[LLM] 条目富化结果已保存: {db_path}")
        result["rss_item_enrich_db_path"] = db_path
        result["rss_item_enrich_by_url"] = mapped

    return result if len(result) > 2 else None


def _build_rss_prompt(*, rss_items: List[Dict[str, Any]], tasks: List[str]) -> str:
    wants_summary = "summary" in tasks
    wants_classify = "classify" in tasks or "classification" in tasks
    wants_cluster = "cluster" in tasks or "clustering" in tasks

    task_text = []
    if wants_summary:
        task_text.append("1) 总结今天这些资讯的整体摘要（<=120字）")
    if wants_classify:
        task_text.append("2) 给出 Top 5 主题 topics（name/count）")
    if wants_cluster:
        task_text.append("3) 将资讯按主题聚类 clusters（每簇一个 label 和 items）")

    lines = [
        "任务：",
        *task_text,
        "",
        "RSS 条目：",
    ]
    for it in rss_items:
        lines.append(f"- {it.get('title','').strip()} | {it.get('feed_name','')} | {it.get('url','')}")
    return "\n".join(lines)


def _map_items_by_url(payload: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    items = payload.get("items") if isinstance(payload, dict) else None
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


def _ensure_items_payload(obj: Any) -> Dict[str, Any]:
    """
    Normalize model output to {"items": [ ... ]}.
    """
    if isinstance(obj, dict):
        items = obj.get("items")
        if isinstance(items, list):
            return {"items": items}
        # single item dict
        if {"url", "title", "summary", "viewpoint"} & set(obj.keys()):
            return {"items": [obj]}
        return {"items": []}
    if isinstance(obj, list):
        return {"items": [x for x in obj if isinstance(x, dict)]}
    return {"items": []}


def _merge_items_lists(cached_items: List[Dict[str, Any]], new_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Merge two item lists by URL, preferring new_items when URL conflicts.
    """
    merged: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []

    def add_list(items: List[Dict[str, Any]], *, prefer: bool) -> None:
        for it in items:
            if not isinstance(it, dict):
                continue
            url = (it.get("url") or "").strip()
            if not url:
                continue
            if url not in merged:
                order.append(url)
                merged[url] = it
            elif prefer:
                merged[url] = it

    add_list(cached_items, prefer=False)
    add_list(new_items, prefer=True)
    return [merged[u] for u in order if u in merged]


def _dedupe_items_by_url(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        url = (it.get("url") or "").strip()
        if not url:
            out.append(it)
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append(it)
    return out


def _coerce_single_item_payload(obj: Any, expected_url: str) -> Optional[Dict[str, Any]]:
    """
    Accept both formats:
    - {"url": "...", "title": "...", "summary": "...", "viewpoint": "..."}
    - {"items": [{"url": "...", ...}]}  (or single-item list)
    """
    if not expected_url:
        return None
    expected_key = normalize_rss_url_key(expected_url)

    if isinstance(obj, dict):
        # wrapped list
        items = obj.get("items")
        if isinstance(items, list):
            if len(items) == 1 and isinstance(items[0], dict):
                return items[0]
            for it in items:
                if not isinstance(it, dict):
                    continue
                url = (it.get("url") or "").strip()
                if url and (url == expected_url or normalize_rss_url_key(url) == expected_key):
                    return it

        # direct object
        url = (obj.get("url") or "").strip()
        if not url or url == expected_url or normalize_rss_url_key(url) == expected_key:
            return obj

    return None


def _debug_print_response(tag: str, resp: Dict[str, Any]) -> None:
    debug_no_truncate = os.environ.get("TREND_LLM_DEBUG_NO_TRUNCATE", "").strip().lower() in {"1", "true", "yes", "on"}
    debug_dump = os.environ.get("TREND_LLM_DEBUG_DUMP", "").strip().lower() in {"1", "true", "yes", "on"}

    raw = resp.get("raw", "")
    parsed = resp.get("json")
    raw_preview = raw if debug_no_truncate else (raw if len(raw) <= 2000 else raw[:2000] + "\n...<truncated>...")
    print(f"[LLM][DEBUG][{tag}] model={resp.get('model')}")
    print(f"[LLM][DEBUG][{tag}] raw_len={len(raw)}")
    print(f"[LLM][DEBUG][{tag}] raw_preview:\n{raw_preview}")
    print(f"[LLM][DEBUG][{tag}] parsed_json={parsed}")
    if debug_dump and raw:
        _debug_dump_text(tag=f"{tag}_raw", text=raw)
    if debug_dump and parsed is not None:
        _debug_dump_text(tag=f"{tag}_parsed_json", text=str(parsed))


def _debug_dump_text(*, tag: str, text: str) -> str:
    base = Path("output") / "llm" / "debug"
    base.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = base / f"{ts}_{tag}.txt"
    path.write_text(text, encoding="utf-8")
    print(f"[LLM][DEBUG] dumped: {path}")
    return str(path)
