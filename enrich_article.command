#!/bin/bash

# 设置当前脚本所在的目录为工作目录（解决双击运行路径问题）
cd "$(dirname "$0")"

echo "================================================"
echo "  NewsPilot - 单篇文章富化工具"
echo "================================================"
echo ""

# 输入 URL
read -r -p "请输入文章链接（URL）: " article_url

# 输入标题（可选）
read -r -p "请输入原始标题（可选，直接回车跳过）: " article_title

echo ""
echo "------------------------------------------------"

# 构造命令参数
cmd_args="--url \"$article_url\""
if [ -n "$article_title" ]; then
    cmd_args="$cmd_args --title \"$article_title\""
fi

# 运行富化
eval uv run python -m NewsPilot enrich-article $cmd_args

echo ""
echo "------------------------------------------------"
read -n 1 -s -r -p "按任意键退出..."
echo
