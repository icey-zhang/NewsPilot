# NewsPilot - AI 驱动的新闻聚合与富化工具

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

NewsPilot 是一个智能新闻聚合平台，能够自动从多个热榜平台和 RSS 订阅源抓取新闻，通过 AI 进行内容富化（摘要、观点提取），并生成美观的 HTML 报告，支持多渠道推送通知。

## ✨ 核心特性

### 📰 多源新闻聚合
- **热榜平台**：支持今日头条、百度热搜、微博、知乎、抖音、B站、财联社、华尔街见闻等
- **RSS 订阅**：支持任意 RSS 源，内置 AI 领域优质信源（机器之心、新智元、量子位等）
- **智能去重**：跨平台相同新闻自动合并

### 🤖 AI 内容富化
- **智能摘要**：自动生成 2-3 句话的内容摘要
- **观点提取**：提炼核心观点和趋势判断
- **全文抓取**：支持抓取文章正文进行深度分析
- **多模型支持**：兼容 OpenAI、Claude、Gemini、GLM、通义千问等

### 📊 多模式报告
| 模式 | 说明 | 适用场景 |
|------|------|----------|
| `daily` | 当日汇总 | 日报总结、全面了解热点 |
| `current` | 当前榜单 | 实时热点追踪 |
| `incremental` | 增量监控 | 只推送新增内容，避免打扰 |

### 🚀 多渠道推送
- 飞书机器人
- 钉钉机器人
- 企业微信
- Telegram
- 邮件
- Slack、Bark、ntfy

### 💾 灵活存储
- **本地存储**：SQLite + HTML/TXT 文件
- **远程存储**：S3 兼容协议（R2/OSS/COS/MinIO）
- **数据同步**：支持 MCP Server 远程查询

## 🚀 快速开始

### 1. 安装依赖

```bash
# 使用 uv（推荐）
uv sync

# 或使用 pip
pip install -e .
```

### 2. 配置

```bash
cp  config/config.yaml.local config/config.yaml
```

编辑 `config/config.yaml.local`，配置：
- RSS 订阅源
- LLM API 信息
- 推送渠道 webhook

### 3. 运行

```bash
# 完整工作流（抓取 + 富化 + 报告）
uv run NewsPilot

# 仅抓取（跳过 LLM 富化）
uv run NewsPilot --no-llm

# 仅生成报告（从已有数据）
uv run NewsPilot --skip-crawl

# 单篇文章富化
uv run NewsPilot enrich-article --url "https://example.com/article"

# 单篇文章仅抓取正文（跳过 LLM 富化），默认保存到 output/fulltext/YYYY-MM-DD/
uv run NewsPilot enrich-article --url "https://example.com/article" --fetch-only

# 仅抓取并打印正文，不保存文件
uv run NewsPilot enrich-article --url "https://example.com/article" --fetch-only --no-save
```

## 📁 项目结构

```
NewsPilot/
├── __main__.py           # CLI 入口
├── context.py            # 应用上下文
├── core/                 # 核心功能
│   ├── analyzer.py       # 热榜分析器
│   ├── frequency.py      # 频率词统计
│   ├── data.py           # 数据处理
│   └── config.py         # 配置管理
├── crawler/              # 爬虫模块
│   ├── fetcher.py        # 热榜抓取
│   └── rss/              # RSS 抓取
│       ├── fetcher.py
│       └── parser.py
├── llm/                  # LLM 富化
│   ├── enrich.py         # 文章富化
│   ├── agent_enrich.py   # Agent 原生富化
│   ├── fulltext.py       # 全文抓取
│   └── openai_compat.py  # OpenAI 兼容接口
├── report/               # 报告生成
│   ├── generator.py      # 报告生成器
│   ├── html.py           # HTML 渲染
│   ├── formatter.py      # 格式化
│   └── history.py        # 历史页面
├── notification/         # 通知推送
│   ├── dispatcher.py     # 调度器
│   ├── senders.py        # 发送器
│   └── renderer.py       # 内容渲染
├── storage/              # 存储管理
│   ├── manager.py        # 存储管理器
│   ├── local.py          # 本地存储
│   ├── remote.py         # 远程存储
│   └── llm_store.py      # LLM 结果存储
├── workflows/            # LangGraph 工作流
│   └── workflow.py
└── utils/                # 工具函数
    ├── time.py
    └── url.py

config/
├── config.yaml           # 主配置
└── frequency_words.txt   # 频率词配置

output/                   # 输出目录
├── YYYY-MM-DD/
│   ├── html/             # HTML 报告
│   └── rss/              # RSS 数据
└── fulltext/
    └── YYYY-MM-DD/
        ├── <url_hash>.txt   # 单篇文章抓取正文
        └── <url_hash>.json  # URL、标题、抓取状态、字符数等元数据
```

## ⚙️ 配置说明

### 基础配置

```yaml
app:
  timezone: "Asia/Shanghai"  # 时区设置

report:
  mode: "daily"              # 报告模式: daily | current | incremental
  display_mode: "keyword"    # 显示模式: keyword | platform
  rank_threshold: 5          # 排名高亮阈值
```

### RSS 配置

```yaml
rss:
  enabled: true
  freshness_filter:
    enabled: true
    max_age_days: 7          # 只推送 7 天内文章
  feeds:
    - id: "jiqizhixin"
      name: "机器之心"
      url: "https://..."
```

### LLM 配置

```yaml
llm:
  enabled: true
  base_url: "https://api.openai.com/v1"
  api_key: "${NP_LLM_API_KEY}"  # 推荐用环境变量
  model: "gpt-4"
  tasks: ["item_enrich"]           # 富化任务
  fulltext:
    enabled: true                  # 启用全文抓取
```

### 推送配置

```yaml
notification:
  enabled: true
  channels:
    feishu:
      webhook_url: "https://open.feishu.cn/..."
    dingtalk:
      webhook_url: "https://oapi.dingtalk.com/..."
```

## 🏗️ 架构设计

### 数据流

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│  热榜平台   │    │  RSS 订阅   │    │  配置文件   │
└──────┬──────┘    └──────┬──────┘    └──────┬──────┘
       │                  │                  │
       └──────────────────┼──────────────────┘
                          ▼
                   ┌─────────────┐
                   │   Crawler   │
                   └──────┬──────┘
                          ▼
                   ┌─────────────┐
                   │   Storage   │
                   │  (SQLite)   │
                   └──────┬──────┘
                          ▼
                   ┌─────────────┐
                   │  LLM Enrich │
                   └──────┬──────┘
                          ▼
                   ┌─────────────┐
                   │   Report    │
                   │  Generator  │
                   └──────┬──────┘
                          ▼
              ┌───────────────────────┐
              │  HTML / Notification  │
              └───────────────────────┘
```

### 双模式运行

- **Legacy 模式**：传统的命令式运行
- **LangGraph 模式**：现代化的工作流编排（默认优先）

## 🛠️ 开发

### 添加新的热榜源

在 `NewsPilot/crawler/fetcher.py` 中添加新的抓取逻辑。

### 自定义 LLM 富化

在 `NewsPilot/llm/prompt.py` 中修改提示词模板。

### 扩展推送渠道

在 `NewsPilot/notification/senders.py` 中添加新的发送器。

## 📄 输出示例

生成的 HTML 报告包含：
- 热点词汇统计（按频次排序）
- 各平台新闻列表
- AI 生成的摘要和观点
- 新增热点高亮
- RSS 订阅更新

报告保存路径：`output/YYYY-MM-DD/html/当日汇总.html`

## 📝 License

MIT License

## 🤝 贡献

欢迎提交 Issue 和 PR！

---

**NewsPilot** - 让信息获取更智能 🚀
