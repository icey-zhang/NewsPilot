"""
InfoAgent CLI entrypoint.

- If `langgraph` is installed, prefers the LangGraph workflow (set `TREND_USE_LANGGRAPH=0` to disable).
- Otherwise falls back to the legacy imperative runner.
"""

from __future__ import annotations

import os

from InfoAgent.legacy.analyzer import NewsAnalyzer


def main():
    """主程序入口"""
    try:
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
