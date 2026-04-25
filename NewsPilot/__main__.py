"""
NewsPilot CLI entrypoint.

- If `langgraph` is installed, prefers the LangGraph workflow (set `NP_USE_LANGGRAPH=0` to disable).
- Otherwise falls back to the legacy imperative runner.

Subcommands:
  (none)              : run the full workflow
  enrich-article      : enrich a single article with title/summary/viewpoint
  llm.agent_enrich    : agent-native enrichment support (fetch/save)

Options:
  --no-llm            : skip LLM enrichment (for agent-native workflow)
  --skip-crawl        : skip crawling, only generate report from stored data
"""

from __future__ import annotations

import argparse
import os
import sys

from NewsPilot.utils.env import get_env


def _cmd_llm_agent_enrich(args):
    """Handle llm.agent_enrich subcommands"""
    from NewsPilot.llm.agent_enrich import main as agent_enrich_main
    # Pass remaining args to agent_enrich CLI
    agent_enrich_main()


def _cmd_enrich_article(args):
    import argparse
    from datetime import datetime
    parser = argparse.ArgumentParser(
        prog="NewsPilot enrich-article",
        description="对单篇文章做「标题 + 总结 + 观点」富化，复用 config.yaml 中的 LLM 配置",
    )
    parser.add_argument("--url", default="", help="文章链接（会自动抓取正文）")
    parser.add_argument("--title", default="", help="原始标题（可选）")
    parser.add_argument("--content", default="", help="文章正文（直接粘贴，跳过网络抓取）")
    parser.add_argument("--date", default="", help="归档日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--output-dir", default="output", help="输出目录（默认 output）")
    parser.add_argument("--fetch-only", action="store_true", help="仅抓取/准备正文，不调用 LLM 富化")
    parser.add_argument("--no-save", action="store_true", help="配合 --fetch-only 使用，仅打印不保存正文")
    opts = parser.parse_args(args)

    if not opts.url and not opts.content:
        parser.error("--url 和 --content 至少提供一个")

    if opts.fetch_only:
        from NewsPilot.core import load_config
        from NewsPilot.llm.enrich import fetch_article_for_enrichment, save_fetched_article

        date = opts.date or datetime.now().strftime("%Y-%m-%d")
        config = load_config()
        llm_cfg = config.get("LLM") or {}
        article = fetch_article_for_enrichment(
            url=opts.url,
            title=opts.title,
            content=opts.content,
            llm_cfg=llm_cfg,
        )
        saved = {}
        if not opts.no_save:
            saved = save_fetched_article(article=article, date=date, output_dir=opts.output_dir)

        content = article.get("content") or ""
        preview = content if len(content) <= 2000 else content[:2000] + "\n...<truncated>..."
        print("\n" + "=" * 60)
        print(f"URL      : {article.get('url') or '(无)'}")
        print(f"标题     : {article.get('title') or '(无)'}")
        print(f"状态     : {article.get('fetch_status') or '(无)'}")
        print(f"字符数   : {len(content)}")
        if saved:
            print(f"正文文件 : {saved.get('text_path')}")
            print(f"元数据   : {saved.get('meta_path')}")
        print("-" * 60)
        print(preview or "(无正文)")
        print("=" * 60)
        return

    from NewsPilot.llm.enrich import enrich_article
    result = enrich_article(
        url=opts.url,
        title=opts.title,
        content=opts.content,
        date=opts.date,
        output_dir=opts.output_dir,
    )

    print("\n" + "=" * 60)
    print(f"URL      : {result.get('url') or '(无)'}")
    print(f"标题     : {result.get('title') or '(无)'}")
    print(f"总结     : {result.get('summary') or '(无)'}")
    print(f"观点     : {result.get('viewpoint') or '(无)'}")
    print(f"模型     : {result.get('model') or '(无)'}")
    print(f"已保存至 : {result.get('db_path') or '(无)'}")
    print("=" * 60)


def main():
    """主程序入口"""
    # 子命令分发
    if len(sys.argv) > 1:
        cmd = sys.argv[1]

        # llm.agent_enrich 子命令
        if cmd == "llm.agent_enrich":
            try:
                # 移除第一个参数，传递给 agent_enrich CLI
                sys.argv = sys.argv[1:]
                _cmd_llm_agent_enrich(sys.argv[1:])
            except Exception as e:
                print(f"❌ 错误: {e}")
                raise
            return

        # enrich-article 子命令
        if cmd == "enrich-article":
            try:
                _cmd_enrich_article(sys.argv[2:])
            except FileNotFoundError as e:
                print(f"❌ 配置文件错误: {e}")
                print("\n请确保 config/config.yaml 存在")
            except Exception as e:
                print(f"❌ 错误: {e}")
                raise
            return

    # 解析全局参数
    no_llm = "--no-llm" in sys.argv
    skip_crawl = "--skip-crawl" in sys.argv
    if no_llm:
        os.environ["NP_SKIP_LLM"] = "1"
        sys.argv = [a for a in sys.argv if a != "--no-llm"]
    if skip_crawl:
        os.environ["NP_SKIP_CRAWL"] = "1"
        sys.argv = [a for a in sys.argv if a != "--skip-crawl"]

    # 默认：完整工作流
    try:
        from NewsPilot.legacy.analyzer import NewsAnalyzer

        use_langgraph = get_env("NP_USE_LANGGRAPH", default="auto").strip().lower()
        if use_langgraph not in {"0", "false", "no"}:
            try:
                from NewsPilot.workflows.run import run_workflow

                print("[Runner] 使用 LangGraph 工作流")
                run_workflow()
                return
            except ImportError:
                pass

        print("[Runner] 使用 Legacy 运行逻辑")
        NewsAnalyzer().run()
    except FileNotFoundError as e:
        print(f"❌ 配置文件错误: {e}")
        print("\n请确保以下文件存在:")
        print("  • config/config.yaml")
        print("  • config/frequency_words.txt")
        print("\n参考项目文档进行正确配置")
    except Exception as e:
        print(f"❌ 程序运行错误: {e}")
        raise


if __name__ == "__main__":
    main()
