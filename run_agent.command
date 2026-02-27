#!/bin/bash

# 设置当前脚本所在的目录为工作目录 (解决双击运行路径问题)
cd "$(dirname "$0")"

# 设置环境变量
export TREND_RSS_DEBUG_FILTER=1
export TREND_LLM_DEBUG=1
export TREND_LLM_DEBUG_DUMP=1

# 运行程序
uv run python -m InfoAgent

# 模拟 Windows 的 pause (等待按任意键退出)
echo "------------------------------------------------"
read -n 1 -s -r -p "按任意键继续..."
echo