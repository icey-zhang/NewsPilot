# InfoAgent

AI 驱动的资讯聚合与富化工具。自动抓取 RSS 订阅源，通过 LLM 对每篇文章生成标题、摘要和观点，并从 OS+AI 视角自动挑选本日最值得关注的 Top3。

## 功能特性

- **RSS 聚合**：支持多订阅源，可配置新鲜度过滤（N 天内）
- **LLM 富化**：对每篇文章自动生成「标题 + 总结 + 观点」（单条模式，避免超时）
- **OS+AI 排序**：以「是否对操作系统/AI 系统设计有启发」为视角，自动打分并选出 Top3
  - 分批处理（每批 5 条），独立超时控制（300s）
  - 打分结果当天缓存，重复运行不重复调用 LLM
- **单篇富化**：支持传入任意 URL 或正文，即时生成摘要（支持微信公众号等）
- **多渠道推送**：飞书、钉钉、企业微信、Telegram、邮件、Bark、Slack、ntfy
- **HTML 报告**：生成可视化日报，含 OS+AI Top3 置顶区块
- **本地存储**：SQLite + 可选 S3 兼容远程存储

## 快速开始

### 环境要求

- Python 3.10+
- [uv](https://docs.astral.sh/uv/)（推荐）

### 安装

```bash
git clone https://github.com/icey-zhang/InfoAgent.git
cd InfoAgent
uv sync
```

### 配置

复制并编辑配置文件：

```bash
cp config/config.yaml config/config.yaml.local
# 编辑 config/config.yaml，填入 LLM 配置和推送渠道
```

**LLM 配置**（`config/config.yaml`）：

```yaml
llm:
  enabled: true
  base_url: "https://api.openai.com"   # 或任意 OpenAI 兼容接口
  api_key: ""                           # 建议用环境变量 TREND_LLM_API_KEY
  model: "gpt-4o-mini"
  tasks: ["item_enrich"]
```

> ⚠️ 强烈建议通过环境变量传入 API Key，避免提交到仓库：
> ```bash
> export TREND_LLM_API_KEY="your-api-key"
> ```

**RSS 订阅源**：在 `config/config.yaml` 的 `rss.feeds` 下添加。

### 运行

```bash
# 完整工作流（抓取 + 富化 + 推送）
uv run python -m InfoAgent

# 单篇文章富化（传 URL，自动抓取正文）
uv run python -m InfoAgent enrich-article --url "https://example.com/article"

# 单篇文章富化（传 URL + 原始标题）
uv run python -m InfoAgent enrich-article --url "https://..." --title "原始标题"

# 单篇文章富化（直接粘贴正文）
uv run python -m InfoAgent enrich-article --content "文章正文..." --title "标题"
```

也可以双击 `enrich_article.command`（macOS）交互式运行单篇富化。

## 项目结构

```
InfoAgent/
├── config/
│   └── config.yaml          # 主配置文件
├── InfoAgent/
│   ├── __main__.py          # CLI 入口
│   ├── llm/
│   │   ├── enrich.py        # LLM 富化主逻辑（含 OS+AI 排序、单篇富化）
│   │   ├── prompt.py        # Prompt 构建
│   │   ├── fulltext.py      # 全文抓取
│   │   └── openai_compat.py # OpenAI 兼容客户端
│   ├── crawler/             # RSS / 热榜抓取
│   ├── storage/             # 数据持久化（SQLite / S3）
│   ├── report/              # HTML 报告生成
│   └── notification/        # 多渠道推送
├── enrich_article.command   # macOS 双击运行的单篇富化工具
└── output/                  # 运行输出（已 gitignore）
```

## 环境变量

| 变量 | 说明 | 默认值 |
|---|---|---|
| `TREND_LLM_API_KEY` | LLM API Key | 读取 config.yaml |
| `TREND_LLM_BASE_URL` | LLM 接口地址 | 读取 config.yaml |
| `TREND_LLM_MODEL` | 模型名称 | 读取 config.yaml |
| `TREND_LLM_TIMEOUT` | 请求超时（秒） | 90 |
| `TREND_LLM_RANK_TIMEOUT` | OS+AI 排序超时（秒） | 300 |
| `TREND_LLM_DEBUG` | 开启调试日志 | 0 |

## Contributors

- [@icey-zhang](https://github.com/icey-zhang)
- [Claude Code](https://claude.ai/claude-code) — Anthropic
- [Codex](https://openai.com/codex) — OpenAI

## License

MIT
