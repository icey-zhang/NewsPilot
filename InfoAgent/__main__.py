"""
InfoAgent CLI entrypoint.

- If `langgraph` is installed, prefers the LangGraph workflow (set `TREND_USE_LANGGRAPH=0` to disable).
- Otherwise falls back to the legacy imperative runner.

Subcommands:
  (none)              : run the full workflow
  enrich-article      : enrich a single article with title/summary/viewpoint
"""

from __future__ import annotations

import os
import sys


def _cmd_enrich_article(args):
    import argparse
    parser = argparse.ArgumentParser(
        prog="python -m InfoAgent enrich-article",
        description="对单篇文章做「标题 + 总结 + 观点」富化，复用 config.yaml 中的 LLM 配置",
    )
    parser.add_argument("--url", default="", help="文章链接（会自动抓取正文）")
    parser.add_argument("--title", default="", help="原始标题（可选）")
    parser.add_argument("--content", default="", help="文章正文（直接粘贴，跳过网络抓取）")
    parser.add_argument("--date", default="", help="归档日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--output-dir", default="output", help="输出目录（默认 output）")
    opts = parser.parse_args(args)

    if not opts.url and not opts.content:
        parser.error("--url 和 --content 至少提供一个")

    from InfoAgent.llm.enrich import enrich_article
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
    if len(sys.argv) > 1 and sys.argv[1] == "enrich-article":
        try:
            _cmd_enrich_article(sys.argv[2:])
        except FileNotFoundError as e:
            print(f"❌ 配置文件错误: {e}")
            print("\n请确保 config/config.yaml 存在")
        except Exception as e:
            print(f"❌ 错误: {e}")
            raise
        return

    # 默认：完整工作流
    try:
        from InfoAgent.legacy.analyzer import NewsAnalyzer

        use_langgraph = os.environ.get("TREND_USE_LANGGRAPH", "auto").strip().lower()
        if use_langgraph not in {"0", "false", "no"}:
            try:
                from InfoAgent.workflows.run import run_workflow

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
