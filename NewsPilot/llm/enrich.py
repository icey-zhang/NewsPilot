# coding=utf-8
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

from NewsPilot.llm.openai_compat import OpenAICompatClient
from NewsPilot.llm.prompt import (
    RSS_ITEM_ENRICH_PROMPT_VERSION,
    build_rss_item_enrich_prompt,
    build_rss_items_enrich_prompt,
)
from NewsPilot.llm.fulltext import FullTextConfig, fetch_article_text
from NewsPilot.storage.llm_store import get_latest_llm_run, save_llm_run
from NewsPilot.utils.env import env_flag, env_int, get_env
from NewsPilot.utils.url import normalize_rss_url_key


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

    # Keep newest items for cost control (default 50; can be overridden via llm.item_enrich.max_items / NP_LLM_ITEM_MAX_ITEMS)
    rss_items = sorted(rss_items, key=lambda x: x.get("published_at", ""), reverse=True)
    rss_items = _dedupe_items_by_url(rss_items)
    item_cfg_raw = llm_cfg.get("ITEM_ENRICH") if isinstance(llm_cfg, dict) else {}
    try:
        configured_max_items = int(
            get_env("NP_LLM_ITEM_MAX_ITEMS", default="")
            or (item_cfg_raw.get("MAX_ITEMS", 0) if isinstance(item_cfg_raw, dict) else 0)
            or 0
        )
    except Exception:
        configured_max_items = 0
    # When aligning with displayed RSS URLs, do not truncate by the default 50 cap.
    keep_max_items = configured_max_items if configured_max_items > 0 else (len(rss_items) if only_urls_set else 50)
    rss_items = rss_items[:keep_max_items]

    base_url = get_env("NP_LLM_BASE_URL", default="") or llm_cfg.get("BASE_URL")
    api_key = get_env("NP_LLM_API_KEY", default="") or llm_cfg.get("API_KEY")
    model = get_env("NP_LLM_MODEL", default="") or llm_cfg.get("MODEL")
    timeout = env_int("NP_LLM_TIMEOUT", default=int(llm_cfg.get("TIMEOUT") or 90))

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
    debug = env_flag("NP_LLM_DEBUG")
    if debug:
        print(f"[LLM][DEBUG] tasks={tasks} wants_item_enrich={wants_item_enrich} wants_summary={wants_summary} wants_classify={wants_classify} wants_cluster={wants_cluster}")

    # Fulltext settings (optional)
    ft_cfg_raw = llm_cfg.get("FULLTEXT") if isinstance(llm_cfg, dict) else {}
    fulltext_enabled = bool(ft_cfg_raw.get("ENABLED", False)) if isinstance(ft_cfg_raw, dict) else False
    if env_flag("NP_LLM_FULLTEXT"):
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
        use_cache = env_flag("NP_LLM_USE_CACHE", default=True)
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
        item_mode = (get_env("NP_LLM_ITEM_MODE", default=item_mode) or item_mode).strip().lower()
        if item_mode not in {"bulk", "single"}:
            item_mode = "bulk"
        item_max_items = int(get_env("NP_LLM_ITEM_MAX_ITEMS", default="") or (item_cfg_raw.get("MAX_ITEMS", 0) if isinstance(item_cfg_raw, dict) else 0) or 0)
        llm_interval_ms = int(get_env("NP_LLM_ITEM_REQUEST_INTERVAL_MS", default="") or (item_cfg_raw.get("REQUEST_INTERVAL_MS", 0) if isinstance(item_cfg_raw, dict) else 0) or 0)
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

        debug_prompt = env_flag("NP_LLM_DEBUG_PROMPT")
        debug_no_truncate = env_flag("NP_LLM_DEBUG_NO_TRUNCATE")
        debug_dump = env_flag("NP_LLM_DEBUG_DUMP")
        debug_each = env_flag("NP_LLM_DEBUG_EACH")
        debug_item_max = env_int("NP_LLM_DEBUG_ITEM_MAX", default=3)
        debug_dump_max = env_int("NP_LLM_DEBUG_DUMP_MAX", default=3)

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

        # 为 OS + AI 相关度自动挑选 TopK 条目
        try:
            ranking = rank_rss_items_for_os_ai(
                client=client,
                date=date,
                rss_items=rss_items,
                enriched_by_url=mapped,
                top_k=3,
            )
            if ranking:
                result["rss_item_os_ai_rank"] = ranking
                db_path2 = save_llm_run(
                    output_dir="output",
                    date=date,
                    kind="rss_item_os_ai_rank",
                    model=ranking.get("model", client.model),
                    payload=ranking,
                )
                print(f"[LLM] OS+AI 排序结果已保存: {db_path2}")
        except Exception as e:
            print(f"[LLM] OS+AI 排序步骤失败，已跳过: {e}")

    return result if len(result) > 2 else None


_RANK_BATCH_SIZE = 5
_RANK_TIMEOUT = env_int("NP_LLM_RANK_TIMEOUT", default=300)


def _load_os_ai_score_cache(output_dir: str, date: str):
    # 从当天已有的 rss_item_os_ai_rank 结果中加载已打分的 URL -> score_info
    from NewsPilot.storage.llm_store import get_latest_llm_run
    from NewsPilot.utils.url import normalize_rss_url_key
    cached_run = get_latest_llm_run(output_dir=output_dir, date=date, kind="rss_item_os_ai_rank")
    if not cached_run:
        return {}
    ranked_items = (cached_run.get("payload") or {}).get("ranked_items") or []
    cache = {}
    for item in ranked_items:
        url = (item.get("url") or "").strip()
        if not url:
            continue
        cache[normalize_rss_url_key(url)] = {
            "url": url,
            "score_os_relevance": item.get("score_os_relevance", 0),
            "score_ai_relevance": item.get("score_ai_relevance", 0),
            "score_novelty": item.get("score_novelty", 0),
            "comment": item.get("comment", ""),
        }
    return cache


def _score_items_batch(client, system: str, batch):
    # 对一批条目打分，返回 {url_key: score_info} 字典
    schema_hint = {
        "items": [
            {
                "url": "string",
                "score_os_relevance": "int (0-10)",
                "score_ai_relevance": "int (0-10)",
                "score_novelty": "int (0-10)",
                "comment": "string",
            }
        ],
    }

    prompt_lines = ["\u8bf7\u4e3a\u4e0b\u9762\u6bcf\u6761\u8d44\u8baf\u6253\u5206\uff0c\u5e76\u8f93\u51fa JSON\uff1a", ""]
    for idx, it in enumerate(batch, start=1):
        prompt_lines.append(str(idx) + ". \u6807\u9898: " + it["title"])
        if it["summary"]:
            prompt_lines.append("   \u6458\u8981: " + it["summary"])
        if it["viewpoint"]:
            prompt_lines.append("   \u89c2\u70b9: " + it["viewpoint"])
        prompt_lines.append("   URL: " + it["url"])
        prompt_lines.append("")
    prompt_lines.append(
        "\u8bf7\u8f93\u51fa\u5982\u4e0b\u683c\u5f0f\u7684 JSON:\n"
        '{"items":[{"url":"...","score_os_relevance":0-10,'
        '"score_ai_relevance":0-10,"score_novelty":0-10,"comment":"..."}]}'
    )
    user = "\n".join(prompt_lines)

    resp = client.chat_json(
        system=system,
        user=user,
        json_schema_hint=schema_hint,
        temperature=0.1,
    )

    from NewsPilot.utils.url import normalize_rss_url_key
    result = {}
    for s in (resp.get("json") or {}).get("items") or []:
        if not isinstance(s, dict):
            continue
        url = (s.get("url") or "").strip()
        if not url:
            continue
        result[normalize_rss_url_key(url)] = s
    return result


def _generate_key_point_35(client, top_items):
    # 对已选出的 top_k 条生成不超过 35 汉字的整体概括
    prompt_lines = ["\u8bf7\u5bf9\u4ee5\u4e0b\u51e0\u6761\u8d44\u8baf\u505a\u6574\u4f53\u6982\u62ec\uff08\u4e0d\u8d85\u8fc735\u4e2a\u6c49\u5b57\uff0c\u5c3d\u91cf\u4e0d\u7528\u6807\u70b9\uff09\uff1a", ""]
    for idx, it in enumerate(top_items, start=1):
        prompt_lines.append(str(idx) + ". " + it.get("title", ""))
        if it.get("comment"):
            prompt_lines.append("   \u63a8\u8350\u7406\u7531: " + it["comment"])
        prompt_lines.append("")
    prompt_lines.append("\u76f4\u63a5\u8f93\u51fa\u6982\u62ec\u6587\u5b57\uff0c\u4e0d\u9700\u8981 JSON \u5305\u88c5\u3002")
    user = "\n".join(prompt_lines)

    resp = client.chat_json(
        system=(
            "\u4f60\u662f\u4e00\u4e2a\u8d44\u6df1\u64cd\u4f5c\u7cfb\u7edf\u5de5\u7a0b\u5e08\u517c AI \u7814\u7a76\u5458\u3002"
            "\u8bf7\u7528\u4e0d\u8d85\u8fc735\u4e2a\u6c49\u5b57\u5bf9\u7ed9\u5b9a\u8d44\u8baf\u505a\u6574\u4f53\u6982\u62ec\uff0c\u8a00\u7b80\u610f\u8d45\uff0c\u5c3d\u91cf\u4e0d\u7528\u6807\u70b9\u3002"
            "\u76f4\u63a5\u8f93\u51fa\u7eaf\u6587\u672c\uff0c\u4e0d\u8981\u5305\u542b JSON \u6216\u591a\u4f59\u683c\u5f0f\u3002"
        ),
        user=user,
        temperature=0.1,
    )
    raw = (resp.get("raw") or "").strip()
    parsed = resp.get("json") or {}
    return (parsed.get("key_point_35") or parsed.get("value") or raw or "").strip()


def rank_rss_items_for_os_ai(
    *,
    client,
    date: str,
    rss_items,
    enriched_by_url,
    top_k: int = 3,
):
    """
    使用 LLM 从 AI + OS 相关度角度为 RSS 条目打分，并选出 TopK。
    - 批次大小 _RANK_BATCH_SIZE，超时 _RANK_TIMEOUT 秒
    - 已打分的 URL 从当天缓存加载，跳过重复打分
    """
    from NewsPilot.utils.url import normalize_rss_url_key
    from NewsPilot.llm.openai_compat import OpenAICompatClient
    if not rss_items or not enriched_by_url:
        return None

    # 使用更长超时的独立客户端
    rank_client = OpenAICompatClient(
        base_url=client.base_url,
        api_key=client.api_key,
        model=client.model,
        timeout=_RANK_TIMEOUT,
    )

    items = sorted(rss_items, key=lambda x: x.get("published_at", ""), reverse=True)

    payload_items = []
    for it in items:
        url = (it.get("url") or "").strip()
        if not url:
            continue
        key = normalize_rss_url_key(url)
        enriched = enriched_by_url.get(key) or {}
        payload_items.append(
            {
                "url": url,
                "title": (enriched.get("title") or it.get("title") or "").strip(),
                "summary": (enriched.get("summary") or "").strip(),
                "viewpoint": (enriched.get("viewpoint") or "").strip(),
            }
        )

    if not payload_items:
        return None

    # 加载当天已有的打分缓存，跳过已打分的 URL
    score_cache = _load_os_ai_score_cache(output_dir="output", date=date)
    hit_count = sum(1 for it in payload_items if normalize_rss_url_key(it["url"]) in score_cache)
    miss_items = [it for it in payload_items if normalize_rss_url_key(it["url"]) not in score_cache]
    if hit_count:
        print(f"[LLM] OS+AI \u6392\u5e8f\u7f13\u5b58\u547d\u4e2d {hit_count} \u6761\uff0c\u5f85\u6253\u5206 {len(miss_items)} \u6761")

    system = (
        "\u4f60\u662f\u4e00\u4e2a\u8d44\u6df1\u64cd\u4f5c\u7cfb\u7edf\u5de5\u7a0b\u5e08\u517c AI \u7814\u7a76\u5458\uff0c\u8d1f\u8d23\u5e2e\u7528\u6237\u6311\u9009\u672c\u5468\u6700\u503c\u5f97\u5173\u6ce8\u7684 AI \u8d44\u8baf\u3002"
        "\u8bf7\u4ece\u201c\u662f\u5426\u80fd\u7ed9\u64cd\u4f5c\u7cfb\u7edf\u8bbe\u8ba1/\u5b9e\u73b0\u5e26\u6765\u542f\u53d1\u201d\u8fd9\u4e2a\u89d2\u5ea6\u8fdb\u884c\u8bc4\u4ef7\uff0c"
        "\u5c24\u5176\u5173\u6ce8\uff1a\u7cfb\u7edf\u67b6\u6784\u3001\u8d44\u6e90\u8c03\u5ea6\u3001\u6027\u80fd\u4f18\u5316\u3001\u5185\u5b58/\u5b58\u50a8\u3001\u865a\u62df\u5316\u3001\u5bb9\u5668\u3001\u7f16\u8bd1\u4e0e\u8fd0\u884c\u65f6\u3001\u7cfb\u7edf\u5b89\u5168\u7b49\u3002"
        "\u540c\u65f6\uff0c\u65b0\u7684\u5927\u6a21\u578b\u3001\u63a8\u7406\u6846\u67b6\u6216\u786c\u4ef6\u5bf9\u7cfb\u7edf\u6808\u4ea7\u751f\u5f71\u54cd\u7684\u5185\u5bb9\u4e5f\u7b97\u6709\u4ef7\u503c\u3002"
        "\u8bf7\u4e25\u683c\u6309\u7167\u6307\u5b9a JSON \u683c\u5f0f\u8f93\u51fa\uff0c\u4e0d\u8981\u5305\u542b\u591a\u4f59\u6587\u5b57\u3002"
    )

    # 分批打分（只处理未命中缓存的条目）
    new_scores = {}
    total_batches = (len(miss_items) + _RANK_BATCH_SIZE - 1) // _RANK_BATCH_SIZE if miss_items else 0
    for batch_idx in range(total_batches):
        batch = miss_items[batch_idx * _RANK_BATCH_SIZE: (batch_idx + 1) * _RANK_BATCH_SIZE]
        print(f"[LLM] OS+AI \u6392\u5e8f batch {batch_idx + 1}/{total_batches}\uff08{len(batch)} \u6761\uff09")
        batch_scores = _score_items_batch(rank_client, system, batch)
        new_scores.update(batch_scores)

    # 合并缓存 + 新打分
    score_by_url = {**score_cache, **new_scores}

    # 汇总打分、计算综合得分
    ranked = []
    for it in payload_items:
        key = normalize_rss_url_key(it["url"])
        score_info = score_by_url.get(key) or {}

        try:
            score_os = int(score_info.get("score_os_relevance") or 0)
        except Exception:
            score_os = 0
        try:
            score_ai = int(score_info.get("score_ai_relevance") or 0)
        except Exception:
            score_ai = 0
        try:
            score_novelty = int(score_info.get("score_novelty") or 0)
        except Exception:
            score_novelty = 0

        final_score = 0.4 * score_os + 0.4 * score_ai + 0.2 * score_novelty

        ranked.append(
            {
                **it,
                "score_os_relevance": score_os,
                "score_ai_relevance": score_ai,
                "score_novelty": score_novelty,
                "final_score": final_score,
                "comment": (score_info.get("comment") or "").strip(),
            }
        )

    if not ranked:
        return None

    ranked.sort(key=lambda x: x["final_score"], reverse=True)
    top_items = ranked[: max(1, top_k)]

    # 用 top_k 条单独生成整体概括
    key_point_35 = ""
    try:
        key_point_35 = _generate_key_point_35(rank_client, top_items)
    except Exception as e:
        print(f"[LLM] key_point_35 \u751f\u6210\u5931\u8d25\uff0c\u5df2\u8df3\u8fc7: {e}")

    return {
        "date": date,
        "model": rank_client.model,
        "ranked_items": ranked,
        "top_items": top_items,
        "key_point_35": key_point_35,
    }


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
    debug_no_truncate = env_flag("NP_LLM_DEBUG_NO_TRUNCATE")
    debug_dump = env_flag("NP_LLM_DEBUG_DUMP")

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


def fetch_article_for_enrichment(
    *,
    url: str = "",
    title: str = "",
    content: str = "",
    llm_cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    准备单篇文章富化所需的输入正文。

    仅负责 fetch/输入准备，不创建 LLM client，不调用模型，不写入 DB。
    """
    if not url and not content:
        raise ValueError("url 和 content 至少提供一个")

    article = {
        "url": url,
        "title": title,
        "content": content,
        "fetched": False,
        "fetch_status": "provided_content" if content else "skipped",
    }
    if content or not url:
        return article

    ft_cfg_raw = llm_cfg.get("FULLTEXT") if isinstance(llm_cfg, dict) else {}
    ft_cfg = FullTextConfig(
        enabled=True,
        timeout=int(ft_cfg_raw.get("TIMEOUT", 15) or 15) if isinstance(ft_cfg_raw, dict) else 15,
        max_bytes=int(ft_cfg_raw.get("MAX_BYTES", 1200000) or 1200000) if isinstance(ft_cfg_raw, dict) else 1200000,
        max_chars=int(ft_cfg_raw.get("MAX_CHARS", 60000) or 60000) if isinstance(ft_cfg_raw, dict) else 60000,
        min_paragraph_chars=int(ft_cfg_raw.get("MIN_PARAGRAPH_CHARS", 60) or 60) if isinstance(ft_cfg_raw, dict) else 60,
        use_proxy=bool(ft_cfg_raw.get("USE_PROXY", False)) if isinstance(ft_cfg_raw, dict) else False,
        proxy_url=(ft_cfg_raw.get("PROXY_URL", "") or "").strip() if isinstance(ft_cfg_raw, dict) else "",
    )
    fetched = fetch_article_text(url, ft_cfg)
    if fetched and len(fetched) >= 100:
        article["content"] = fetched
        article["fetched"] = True
        article["fetch_status"] = "ok"
        print(f"[enrich-article] 全文抓取成功，字符数: {len(fetched)}")
    else:
        article["fetch_status"] = "too_short_or_failed"
        print("[enrich-article] 全文抓取失败或内容过短，仅凭标题/URL 做总结")
    return article


def save_fetched_article(
    *,
    article: Dict[str, Any],
    date: str = "",
    output_dir: str = "output",
) -> Dict[str, str]:
    """
    保存单篇文章抓取结果到 output/fulltext/{date}/。
    """
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    url = (article.get("url") or "").strip()
    title = (article.get("title") or "").strip()
    content = article.get("content") or ""
    identity = url or title or content[:200]
    file_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]

    base = Path(output_dir) / "fulltext" / date
    base.mkdir(parents=True, exist_ok=True)
    text_path = base / f"{file_id}.txt"
    meta_path = base / f"{file_id}.json"

    text_path.write_text(content, encoding="utf-8")
    meta = {
        "date": date,
        "url": url,
        "title": title,
        "fetched": bool(article.get("fetched", False)),
        "fetch_status": article.get("fetch_status") or "",
        "content_chars": len(content),
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "text_path": str(text_path),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"text_path": str(text_path), "meta_path": str(meta_path)}


def llm_enrich_article(
    *,
    url: str = "",
    title: str = "",
    content: str = "",
    date: str = "",
    output_dir: str = "output",
    llm_cfg: Optional[Dict[str, Any]] = None,
) -> dict:
    """
    对已准备好的单篇文章输入执行 LLM 富化并保存结果。

    不负责网络抓取；调用前如需正文请先执行 fetch_article_for_enrichment。
    """
    from NewsPilot.core import load_config

    if not url and not content:
        raise ValueError("url 和 content 至少提供一个")

    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    if llm_cfg is None:
        config = load_config()
        llm_cfg = config.get("LLM") or {}

    base_url = get_env("NP_LLM_BASE_URL", default="") or llm_cfg.get("BASE_URL")
    api_key = get_env("NP_LLM_API_KEY", default="") or llm_cfg.get("API_KEY")
    model = get_env("NP_LLM_MODEL", default="") or llm_cfg.get("MODEL")
    timeout = env_int("NP_LLM_TIMEOUT", default=int(llm_cfg.get("TIMEOUT") or 90))

    client = OpenAICompatClient(base_url=base_url, api_key=api_key, model=model, timeout=timeout)

    item = {"url": url, "title": title, "content": content}
    system, user, schema_hint = build_rss_item_enrich_prompt(
        item=item,
        include_content=bool(content),
        language="zh",
    )

    print(f"[enrich-article] 调用 LLM（model={client.model}）...")
    resp = client.chat_json(system=system, user=user, json_schema_hint=schema_hint, temperature=0.2)

    data = resp.get("json") or {}
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        matched = None
        for candidate in data["items"]:
            if not isinstance(candidate, dict):
                continue
            if (candidate.get("url") or "").strip() == url:
                matched = candidate
                break
        data = matched or next((x for x in data["items"] if isinstance(x, dict)), {})
    result = {
        "url": url,
        "title": (data.get("title") or title or "").strip(),
        "summary": (data.get("summary") or "").strip(),
        "viewpoint": (data.get("viewpoint") or "").strip(),
        "model": resp.get("model", client.model),
        "raw": resp.get("raw") or "",
    }
    if not any((result["title"], result["summary"], result["viewpoint"])):
        raw_preview = (resp.get("raw") or "").strip()
        if len(raw_preview) > 500:
            raw_preview = raw_preview[:500] + "\n...<truncated>..."
        print("[enrich-article] WARNING: LLM 返回未解析出 title/summary/viewpoint")
        if raw_preview:
            print(f"[enrich-article] raw preview:\n{raw_preview}")

    db_path = save_llm_run(
        output_dir=output_dir,
        date=date,
        kind="article_enrich",
        model=result["model"],
        payload={
            "date": date,
            "url": url,
            "input_title": title,
            "has_fulltext": bool(content),
            "result": result,
            "parsed_json": resp.get("json"),
            "raw": resp.get("raw"),
        },
    )
    result["db_path"] = db_path
    print(f"[enrich-article] 结果已保存: {db_path}")
    return result


def enrich_article(
    *,
    url: str = "",
    title: str = "",
    content: str = "",
    date: str = "",
    output_dir: str = "output",
) -> dict:
    """
    对单篇文章做「标题 + 总结 + 观点」富化。
    复用 config.yaml 中的 llm 配置和 fulltext 配置。

    参数:
        url     : 文章链接（传入后自动抓取正文，除非同时传了 content）
        title   : 原始标题（可选，有助于 LLM 理解文章）
        content : 文章正文（可选，传入则跳过网络抓取）
        date    : 日期字符串（默认今天，用于归档到 output/llm/{date}.db）
        output_dir: 输出目录，默认 output

    返回:
        {"url": ..., "title": ..., "summary": ..., "viewpoint": ..., "model": ..., "db_path": ...}
    """
    from NewsPilot.core import load_config

    if not url and not content:
        raise ValueError("url 和 content 至少提供一个")

    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    # 加载配置
    config = load_config()
    llm_cfg = config.get("LLM") or {}

    article = fetch_article_for_enrichment(
        url=url,
        title=title,
        content=content,
        llm_cfg=llm_cfg,
    )
    return llm_enrich_article(
        url=article["url"],
        title=article["title"],
        content=article["content"],
        date=date,
        output_dir=output_dir,
        llm_cfg=llm_cfg,
    )
