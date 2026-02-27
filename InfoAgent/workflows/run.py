# coding=utf-8
from __future__ import annotations

from typing import Any, Dict, List, Optional


def run_workflow(
    *,
    include_news: bool = True,
    include_rss: bool = True,
    llm_tasks: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Run InfoAgent via LangGraph workflow.

    Raises:
        ImportError: if `langgraph` is not installed.
    """
    from .workflow import build_and_run

    return build_and_run(
        include_news=include_news,
        include_rss=include_rss,
        llm_tasks=llm_tasks or [],
    )
