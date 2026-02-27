# coding=utf-8
"""
Legacy InfoAgent runtime (pre-LangGraph).

This module keeps the original imperative workflow so that:
- CLI behavior stays compatible
- LangGraph workflow can reuse the same implementation pieces
"""

import os
import webbrowser
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import requests

from InfoAgent.context import AppContext
from InfoAgent import __version__
from InfoAgent.core import load_config
from InfoAgent.core.analyzer import convert_keyword_stats_to_platform_stats
from InfoAgent.crawler import DataFetcher
from InfoAgent.storage import convert_crawl_results_to_news_data
from InfoAgent.utils.time import is_within_days


def check_version_update(
    current_version: str, version_url: str, proxy_url: Optional[str] = None
) -> Tuple[bool, Optional[str]]:
    """检查版本更新"""
    try:
        proxies = None
        if proxy_url:
            proxies = {"http": proxy_url, "https": proxy_url}

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/plain, */*",
            "Cache-Control": "no-cache",
        }

        response = requests.get(
            version_url, proxies=proxies, headers=headers, timeout=10
        )
        response.raise_for_status()

        remote_version = response.text.strip()
        print(f"当前版本: {current_version}, 远程版本: {remote_version}")

        def parse_version(version_str: str):
            try:
                parts = version_str.strip().split(".")
                if len(parts) != 3:
                    raise ValueError("版本号格式不正确")
                return int(parts[0]), int(parts[1]), int(parts[2])
            except Exception:
                return 0, 0, 0

        current_tuple = parse_version(current_version)
        remote_tuple = parse_version(remote_version)

        need_update = current_tuple < remote_tuple
        return need_update, remote_version if need_update else None

    except Exception as e:
        print(f"版本检查失败: {e}")
        return False, None


class NewsAnalyzer:
    """新闻分析器（legacy 实现）"""

    MODE_STRATEGIES = {
        "incremental": {
            "mode_name": "增量模式",
            "description": "增量模式（只关注新增新闻，无新增时不推送）",
            "realtime_report_type": "实时增量",
            "summary_report_type": "当日汇总",
            "should_send_realtime": True,
            "should_generate_summary": True,
            "summary_mode": "daily",
        },
        "current": {
            "mode_name": "当前榜单模式",
            "description": "当前榜单模式（当前榜单匹配新闻 + 新增新闻区域 + 按时推送）",
            "realtime_report_type": "实时当前榜单",
            "summary_report_type": "当前榜单汇总",
            "should_send_realtime": True,
            "should_generate_summary": True,
            "summary_mode": "current",
        },
        "daily": {
            "mode_name": "当日汇总模式",
            "description": "当日汇总模式（所有匹配新闻 + 新增新闻区域 + 按时推送）",
            "realtime_report_type": "",
            "summary_report_type": "当日汇总",
            "should_send_realtime": False,
            "should_generate_summary": True,
            "summary_mode": "daily",
        },
    }

    def __init__(self):
        print("正在加载配置...")
        config = load_config()
        print(f"InfoAgent v{__version__} 配置加载完成")
        print(f"监控平台数量: {len(config['PLATFORMS'])}")
        print(f"时区: {config.get('TIMEZONE', 'Asia/Shanghai')}")

        self.ctx = AppContext(config)

        self.request_interval = self.ctx.config["REQUEST_INTERVAL"]
        self.report_mode = self.ctx.config["REPORT_MODE"]
        self.rank_threshold = self.ctx.rank_threshold
        self.is_github_actions = os.environ.get("GITHUB_ACTIONS") == "true"
        self.is_docker_container = self._detect_docker_environment()
        self.update_info = None
        self.proxy_url = None
        self._setup_proxy()
        self.data_fetcher = DataFetcher(self.proxy_url)

        self._init_storage_manager()

        if self.is_github_actions:
            self._check_version_update()

    def _init_storage_manager(self) -> None:
        env_retention = os.environ.get("STORAGE_RETENTION_DAYS", "").strip()
        if env_retention:
            self.ctx.config["STORAGE"]["RETENTION_DAYS"] = int(env_retention)

        self.storage_manager = self.ctx.get_storage_manager()
        print(f"存储后端: {self.storage_manager.backend_name}")

        retention_days = self.ctx.config.get("STORAGE", {}).get("RETENTION_DAYS", 0)
        if retention_days > 0:
            print(f"数据保留天数: {retention_days} 天")

    def _detect_docker_environment(self) -> bool:
        try:
            if os.environ.get("DOCKER_CONTAINER") == "true":
                return True
            if os.path.exists("/.dockerenv"):
                return True
            return False
        except Exception:
            return False

    def _should_open_browser(self) -> bool:
        # MCP/服务端触发时通常不应打开浏览器
        open_flag = os.environ.get("TREND_OPEN_BROWSER", "1").strip().lower()
        if open_flag in {"0", "false", "no"}:
            return False
        return not self.is_github_actions and not self.is_docker_container

    def _setup_proxy(self) -> None:
        if not self.is_github_actions and self.ctx.config["USE_PROXY"]:
            self.proxy_url = self.ctx.config["DEFAULT_PROXY"]
            print("本地环境，使用代理")
        elif not self.is_github_actions and not self.ctx.config["USE_PROXY"]:
            print("本地环境，未启用代理")
        else:
            print("GitHub Actions环境，不使用代理")

    def _check_version_update(self) -> None:
        try:
            need_update, remote_version = check_version_update(
                __version__, self.ctx.config["VERSION_CHECK_URL"], self.proxy_url
            )

            if need_update and remote_version:
                self.update_info = {
                    "current_version": __version__,
                    "remote_version": remote_version,
                }
                print(f"发现新版本: {remote_version} (当前: {__version__})")
            else:
                print("版本检查完成，当前为最新版本")
        except Exception as e:
            print(f"版本检查出错: {e}")

    def _get_mode_strategy(self) -> Dict:
        return self.MODE_STRATEGIES.get(self.report_mode, self.MODE_STRATEGIES["daily"])

    def _has_notification_configured(self) -> bool:
        cfg = self.ctx.config
        return any(
            [
                cfg["FEISHU_WEBHOOK_URL"],
                cfg["DINGTALK_WEBHOOK_URL"],
                cfg["WEWORK_WEBHOOK_URL"],
                (cfg["TELEGRAM_BOT_TOKEN"] and cfg["TELEGRAM_CHAT_ID"]),
                (cfg["EMAIL_FROM"] and cfg["EMAIL_PASSWORD"] and cfg["EMAIL_TO"]),
                (cfg["NTFY_SERVER_URL"] and cfg["NTFY_TOPIC"]),
                cfg["BARK_URL"],
                cfg["SLACK_WEBHOOK_URL"],
            ]
        )

    def _has_valid_content(self, stats: List[Dict], new_titles: Optional[Dict] = None) -> bool:
        if self.report_mode == "incremental":
            has_new_titles = bool(
                new_titles and any(len(titles) > 0 for titles in new_titles.values())
            )
            has_matched_news = any(stat["count"] > 0 for stat in stats)
            return has_new_titles and has_matched_news
        if self.report_mode == "current":
            return any(stat["count"] > 0 for stat in stats)
        has_matched_news = any(stat["count"] > 0 for stat in stats)
        has_new_news = bool(new_titles and any(len(titles) > 0 for titles in new_titles.values()))
        return has_matched_news or has_new_news

    def _load_analysis_data(self, quiet: bool = False):
        try:
            current_platform_ids = self.ctx.platform_ids
            if not quiet:
                print(f"当前监控平台: {current_platform_ids}")

            all_results, id_to_name, title_info = self.ctx.read_today_titles(
                current_platform_ids, quiet=quiet
            )

            if not all_results:
                print("没有找到当天的数据")
                return None

            total_titles = sum(len(titles) for titles in all_results.values())
            if not quiet:
                print(f"读取到 {total_titles} 个标题（已按当前监控平台过滤）")

            new_titles = self.ctx.detect_new_titles(current_platform_ids, quiet=quiet)
            word_groups, filter_words, global_filters = self.ctx.load_frequency_words()

            return (
                all_results,
                id_to_name,
                title_info,
                new_titles,
                word_groups,
                filter_words,
                global_filters,
            )
        except Exception as e:
            print(f"数据加载失败: {e}")
            return None

    def _prepare_current_title_info(self, results: Dict, time_info: str) -> Dict:
        title_info: Dict = {}
        for source_id, titles_data in results.items():
            title_info[source_id] = {}
            for title, title_data in titles_data.items():
                ranks = title_data.get("ranks", [])
                url = title_data.get("url", "")
                mobile_url = title_data.get("mobileUrl", "")

                title_info[source_id][title] = {
                    "first_time": time_info,
                    "last_time": time_info,
                    "count": 1,
                    "ranks": ranks,
                    "url": url,
                    "mobileUrl": mobile_url,
                }
        return title_info

    def _run_analysis_pipeline(
        self,
        data_source: Dict,
        mode: str,
        title_info: Dict,
        new_titles: Dict,
        word_groups: List[Dict],
        filter_words: List[str],
        id_to_name: Dict,
        failed_ids: Optional[List] = None,
        is_daily_summary: bool = False,
        global_filters: Optional[List[str]] = None,
        quiet: bool = False,
        rss_items: Optional[List[Dict]] = None,
        rss_new_items: Optional[List[Dict]] = None,
    ):
        stats, total_titles = self.ctx.count_frequency(
            data_source,
            word_groups,
            filter_words,
            id_to_name,
            title_info=title_info,
            new_titles=new_titles,
            mode=mode,
            global_filters=global_filters,
            quiet=quiet,
        )

        if self.ctx.display_mode == "platform" and stats:
            stats = convert_keyword_stats_to_platform_stats(
                stats,
                self.ctx.weight_config,
                self.ctx.rank_threshold,
            )

        html_file = None
        if self.ctx.config["STORAGE"]["FORMATS"]["HTML"]:
            html_file = self.ctx.generate_html(
                stats,
                total_titles,
                failed_ids=failed_ids,
                new_titles=new_titles,
                id_to_name=id_to_name,
                mode=mode,
                is_daily_summary=is_daily_summary,
                update_info=self.update_info if self.ctx.config["SHOW_VERSION_UPDATE"] else None,
                rss_items=rss_items,
                rss_new_items=rss_new_items,
            )

        return stats, html_file

    def _send_notification_if_needed(
        self,
        stats: List[Dict],
        report_type: str,
        mode: str,
        failed_ids: Optional[List] = None,
        new_titles: Optional[Dict] = None,
        id_to_name: Optional[Dict] = None,
        html_file_path: Optional[str] = None,
        rss_items: Optional[List[Dict]] = None,
        rss_new_items: Optional[List[Dict]] = None,
    ) -> bool:
        has_notification = self._has_notification_configured()
        cfg = self.ctx.config

        has_news_content = self._has_valid_content(stats, new_titles)
        has_rss_content = bool(rss_items and len(rss_items) > 0)
        has_any_content = has_news_content or has_rss_content

        news_count = sum(len(stat.get("titles", [])) for stat in stats) if stats else 0
        rss_count = len(rss_items) if rss_items else 0

        if cfg["ENABLE_NOTIFICATION"] and has_notification and has_any_content:
            content_parts = []
            if news_count > 0:
                content_parts.append(f"热榜 {news_count} 条")
            if rss_count > 0:
                content_parts.append(f"RSS {rss_count} 条")
            total_count = news_count + rss_count
            print(f"[推送] 准备发送：{' + '.join(content_parts)}，合计 {total_count} 条")

            if cfg["PUSH_WINDOW"]["ENABLED"]:
                push_manager = self.ctx.create_push_manager()
                time_range_start = cfg["PUSH_WINDOW"]["TIME_RANGE"]["START"]
                time_range_end = cfg["PUSH_WINDOW"]["TIME_RANGE"]["END"]

                if not push_manager.is_in_time_range(time_range_start, time_range_end):
                    now = self.ctx.get_time()
                    print(
                        f"推送窗口控制：当前时间 {now.strftime('%H:%M')} 不在推送时间窗口 {time_range_start}-{time_range_end} 内，跳过推送"
                    )
                    return False

                if cfg["PUSH_WINDOW"]["ONCE_PER_DAY"]:
                    if push_manager.has_pushed_today():
                        print("推送窗口控制：今天已推送过，跳过本次推送")
                        return False
                    print("推送窗口控制：今天首次推送")

            report_data = self.ctx.prepare_report(stats, failed_ids, new_titles, id_to_name, mode)
            update_info_to_send = self.update_info if cfg["SHOW_VERSION_UPDATE"] else None

            dispatcher = self.ctx.create_notification_dispatcher()
            results = dispatcher.dispatch_all(
                report_data=report_data,
                report_type=report_type,
                update_info=update_info_to_send,
                proxy_url=self.proxy_url,
                mode=mode,
                html_file_path=html_file_path,
                rss_items=rss_items,
                rss_new_items=rss_new_items,
            )

            if not results:
                print("未配置任何通知渠道，跳过通知发送")
                return False

            if (
                cfg["PUSH_WINDOW"]["ENABLED"]
                and cfg["PUSH_WINDOW"]["ONCE_PER_DAY"]
                and any(results.values())
            ):
                push_manager = self.ctx.create_push_manager()
                push_manager.record_push(report_type)

            return True

        if cfg["ENABLE_NOTIFICATION"] and not has_notification:
            print("⚠️ 警告：通知功能已启用但未配置任何通知渠道，将跳过通知发送")
        elif not cfg["ENABLE_NOTIFICATION"]:
            print(f"跳过{report_type}通知：通知功能已禁用")
        elif cfg["ENABLE_NOTIFICATION"] and has_notification and not has_any_content:
            mode_strategy = self._get_mode_strategy()
            if "实时" in report_type:
                if self.report_mode == "incremental":
                    has_new = bool(new_titles and any(len(titles) > 0 for titles in new_titles.values()))
                    if not has_new and not has_rss_content:
                        print("跳过实时推送通知：增量模式下未检测到新增的新闻和RSS")
                    elif not has_new:
                        print("跳过实时推送通知：增量模式下新增新闻未匹配到关键词")
                else:
                    print(f"跳过实时推送通知：{mode_strategy['mode_name']}下未检测到匹配的新闻")
            else:
                print(f"跳过{mode_strategy['summary_report_type']}通知：未匹配到有效的新闻内容")

        return False

    def _generate_summary_report(
        self,
        mode_strategy: Dict,
        rss_items: Optional[List[Dict]] = None,
        rss_new_items: Optional[List[Dict]] = None,
    ) -> Optional[str]:
        summary_type = "当前榜单汇总" if mode_strategy["summary_mode"] == "current" else "当日汇总"
        print(f"生成{summary_type}报告...")

        analysis_data = self._load_analysis_data()
        if not analysis_data:
            return None

        all_results, id_to_name, title_info, new_titles, word_groups, filter_words, global_filters = analysis_data

        stats, html_file = self._run_analysis_pipeline(
            all_results,
            mode_strategy["summary_mode"],
            title_info,
            new_titles,
            word_groups,
            filter_words,
            id_to_name,
            is_daily_summary=True,
            global_filters=global_filters,
            rss_items=rss_items,
            rss_new_items=rss_new_items,
        )

        if html_file:
            print(f"{summary_type}报告已生成: {html_file}")

        self._send_notification_if_needed(
            stats,
            mode_strategy["summary_report_type"],
            mode_strategy["summary_mode"],
            failed_ids=[],
            new_titles=new_titles,
            id_to_name=id_to_name,
            html_file_path=html_file,
            rss_items=rss_items,
            rss_new_items=rss_new_items,
        )

        return html_file

    def _generate_summary_html(
        self,
        mode: str = "daily",
        rss_items: Optional[List[Dict]] = None,
        rss_new_items: Optional[List[Dict]] = None,
    ) -> Optional[str]:
        summary_type = "当前榜单汇总" if mode == "current" else "当日汇总"
        print(f"生成{summary_type}HTML...")

        analysis_data = self._load_analysis_data(quiet=True)
        if not analysis_data:
            return None

        all_results, id_to_name, title_info, new_titles, word_groups, filter_words, global_filters = analysis_data

        _, html_file = self._run_analysis_pipeline(
            all_results,
            mode,
            title_info,
            new_titles,
            word_groups,
            filter_words,
            id_to_name,
            is_daily_summary=True,
            global_filters=global_filters,
            quiet=True,
            rss_items=rss_items,
            rss_new_items=rss_new_items,
        )

        if html_file:
            print(f"{summary_type}HTML已生成: {html_file}")
        return html_file

    def _initialize_and_check_config(self) -> None:
        now = self.ctx.get_time()
        print(f"当前北京时间: {now.strftime('%Y-%m-%d %H:%M:%S')}")

        if not self.ctx.config["ENABLE_CRAWLER"]:
            print("爬虫功能已禁用（ENABLE_CRAWLER=False），程序退出")
            return

        has_notification = self._has_notification_configured()
        if not self.ctx.config["ENABLE_NOTIFICATION"]:
            print("通知功能已禁用（ENABLE_NOTIFICATION=False），将只进行数据抓取")
        elif not has_notification:
            print("未配置任何通知渠道，将只进行数据抓取，不发送通知")
        else:
            print("通知功能已启用，将发送通知")

        mode_strategy = self._get_mode_strategy()
        print(f"报告模式: {self.report_mode}")
        print(f"运行模式: {mode_strategy['description']}")

    def _crawl_data(self) -> Tuple[Dict, Dict, List]:
        ids = []
        for platform in self.ctx.platforms:
            if "name" in platform:
                ids.append((platform["id"], platform["name"]))
            else:
                ids.append(platform["id"])

        print(f"配置的监控平台: {[p.get('name', p['id']) for p in self.ctx.platforms]}")
        print(f"开始爬取数据，请求间隔 {self.request_interval} 毫秒")
        Path("output").mkdir(parents=True, exist_ok=True)

        results, id_to_name, failed_ids = self.data_fetcher.crawl_websites(
            ids, self.request_interval
        )

        crawl_time = self.ctx.format_time()
        crawl_date = self.ctx.format_date()
        news_data = convert_crawl_results_to_news_data(
            results, id_to_name, failed_ids, crawl_time, crawl_date
        )

        if self.storage_manager.save_news_data(news_data):
            print(f"数据已保存到存储后端: {self.storage_manager.backend_name}")

        txt_file = self.storage_manager.save_txt_snapshot(news_data)
        if txt_file:
            print(f"TXT 快照已保存: {txt_file}")

        if self.ctx.config["STORAGE"]["FORMATS"]["TXT"]:
            title_file = self.ctx.save_titles(results, id_to_name, failed_ids)
            print(f"标题已保存到: {title_file}")

        return results, id_to_name, failed_ids

    def _crawl_rss_data(self):
        if not self.ctx.rss_enabled:
            return None, None

        rss_feeds = self.ctx.rss_feeds
        if not rss_feeds:
            print("[RSS] 未配置任何 RSS 源")
            return None, None

        try:
            from InfoAgent.crawler.rss import RSSFetcher, RSSFeedConfig

            feeds = []
            for feed_config in rss_feeds:
                max_age_days_raw = feed_config.get("max_age_days")
                max_age_days = None
                if max_age_days_raw is not None:
                    try:
                        max_age_days = int(max_age_days_raw)
                        if max_age_days < 0:
                            feed_id = feed_config.get("id", "unknown")
                            print(f"[警告] RSS feed '{feed_id}' 的 max_age_days 为负数，将使用全局默认值")
                            max_age_days = None
                    except (ValueError, TypeError):
                        feed_id = feed_config.get("id", "unknown")
                        print(f"[警告] RSS feed '{feed_id}' 的 max_age_days 格式错误：{max_age_days_raw}")
                        max_age_days = None

                feed = RSSFeedConfig(
                    id=feed_config.get("id", ""),
                    name=feed_config.get("name", ""),
                    url=feed_config.get("url", ""),
                    max_items=feed_config.get("max_items", 50),
                    enabled=feed_config.get("enabled", True),
                    max_age_days=max_age_days,
                )
                if feed.id and feed.url and feed.enabled:
                    feeds.append(feed)

            if not feeds:
                print("[RSS] 没有启用的 RSS 源")
                return None, None

            rss_config = self.ctx.rss_config
            rss_proxy_url = rss_config.get("PROXY_URL", "") or self.proxy_url or ""
            timezone = self.ctx.config.get("TIMEZONE", "Asia/Shanghai")
            freshness_config = rss_config.get("FRESHNESS_FILTER", {})
            freshness_enabled = freshness_config.get("ENABLED", True)
            default_max_age_days = freshness_config.get("MAX_AGE_DAYS", 3)

            fetcher = RSSFetcher(
                feeds=feeds,
                request_interval=rss_config.get("REQUEST_INTERVAL", 2000),
                timeout=rss_config.get("TIMEOUT", 15),
                use_proxy=rss_config.get("USE_PROXY", False),
                proxy_url=rss_proxy_url,
                timezone=timezone,
                freshness_enabled=freshness_enabled,
                default_max_age_days=default_max_age_days,
            )

            rss_data = fetcher.fetch_all()

            if self.storage_manager.save_rss_data(rss_data):
                print("[RSS] 数据已保存到存储后端")
                return self._process_rss_data_by_mode(rss_data)
            print("[RSS] 数据保存失败")
            return None, None

        except ImportError as e:
            print(f"[RSS] 缺少依赖: {e}")
            print("[RSS] 请安装 feedparser: pip install feedparser")
            return None, None
        except Exception as e:
            print(f"[RSS] 抓取失败: {e}")
            return None, None

    def _process_rss_data_by_mode(self, rss_data):
        from InfoAgent.core.analyzer import count_rss_frequency

        rss_config = self.ctx.rss_config
        if not rss_config.get("NOTIFICATION", {}).get("ENABLED", False):
            return None, None

        try:
            word_groups, filter_words, global_filters = self.ctx.load_frequency_words()
        except FileNotFoundError:
            word_groups, filter_words, global_filters = [], [], []

        timezone = self.ctx.timezone
        max_news_per_keyword = self.ctx.config.get("MAX_NEWS_PER_KEYWORD", 0)
        sort_by_position_first = self.ctx.config.get("SORT_BY_POSITION_FIRST", False)

        rss_stats = None
        rss_new_stats = None

        new_items_dict = self.storage_manager.detect_new_rss_items(rss_data)
        new_items_list = None
        if new_items_dict:
            new_items_list = self._convert_rss_items_to_list(new_items_dict, rss_data.id_to_name)
            if new_items_list:
                print(f"[RSS] 检测到 {len(new_items_list)} 条新增")

        if self.report_mode == "incremental":
            if not new_items_list:
                print("[RSS] 增量模式：没有新增 RSS 条目")
                return None, None

            rss_stats, _total = count_rss_frequency(
                rss_items=new_items_list,
                word_groups=word_groups,
                filter_words=filter_words,
                global_filters=global_filters,
                new_items=new_items_list,
                max_news_per_keyword=max_news_per_keyword,
                sort_by_position_first=sort_by_position_first,
                timezone=timezone,
                rank_threshold=self.rank_threshold,
                quiet=False,
            )
            if not rss_stats:
                print("[RSS] 增量模式：关键词匹配后没有内容")
                return None, None

        elif self.report_mode == "current":
            latest_data = self.storage_manager.get_latest_rss_data(rss_data.date)
            if not latest_data:
                print("[RSS] 当前榜单模式：没有 RSS 数据")
                return None, None

            all_items_list = self._convert_rss_items_to_list(latest_data.items, latest_data.id_to_name)
            rss_stats, _total = count_rss_frequency(
                rss_items=all_items_list,
                word_groups=word_groups,
                filter_words=filter_words,
                global_filters=global_filters,
                new_items=new_items_list,
                max_news_per_keyword=max_news_per_keyword,
                sort_by_position_first=sort_by_position_first,
                timezone=timezone,
                rank_threshold=self.rank_threshold,
                quiet=False,
            )
            if not rss_stats:
                print("[RSS] 当前榜单模式：关键词匹配后没有内容")
                return None, None

            if new_items_list:
                rss_new_stats, _ = count_rss_frequency(
                    rss_items=new_items_list,
                    word_groups=word_groups,
                    filter_words=filter_words,
                    global_filters=global_filters,
                    new_items=new_items_list,
                    max_news_per_keyword=max_news_per_keyword,
                    sort_by_position_first=sort_by_position_first,
                    timezone=timezone,
                    rank_threshold=self.rank_threshold,
                    quiet=True,
                )

        else:
            all_data = self.storage_manager.get_rss_data(rss_data.date)
            if not all_data:
                print("[RSS] 当日汇总模式：没有 RSS 数据")
                return None, None

            all_items_list = self._convert_rss_items_to_list(all_data.items, all_data.id_to_name)
            rss_stats, _total = count_rss_frequency(
                rss_items=all_items_list,
                word_groups=word_groups,
                filter_words=filter_words,
                global_filters=global_filters,
                new_items=new_items_list,
                max_news_per_keyword=max_news_per_keyword,
                sort_by_position_first=sort_by_position_first,
                timezone=timezone,
                rank_threshold=self.rank_threshold,
                quiet=False,
            )
            if not rss_stats:
                print("[RSS] 当日汇总模式：关键词匹配后没有内容")
                return None, None

            if new_items_list:
                rss_new_stats, _ = count_rss_frequency(
                    rss_items=new_items_list,
                    word_groups=word_groups,
                    filter_words=filter_words,
                    global_filters=global_filters,
                    new_items=new_items_list,
                    max_news_per_keyword=max_news_per_keyword,
                    sort_by_position_first=sort_by_position_first,
                    timezone=timezone,
                    rank_threshold=self.rank_threshold,
                    quiet=True,
                )

        return rss_stats, rss_new_stats

    def _convert_rss_items_to_list(self, items_dict: Dict, id_to_name: Dict) -> List[Dict]:
        rss_items: List[Dict] = []
        filtered_count = 0

        rss_config = self.ctx.rss_config
        freshness_config = rss_config.get("FRESHNESS_FILTER", {})
        freshness_enabled = freshness_config.get("ENABLED", True)
        default_max_age_days = freshness_config.get("MAX_AGE_DAYS", 3)
        timezone = self.ctx.config.get("TIMEZONE", "Asia/Shanghai")

        feed_max_age_map: Dict[str, int] = {}
        for feed_cfg in self.ctx.rss_feeds:
            feed_id = feed_cfg.get("id", "")
            max_age = feed_cfg.get("max_age_days")
            if max_age is not None:
                try:
                    feed_max_age_map[feed_id] = int(max_age)
                except (ValueError, TypeError):
                    pass

        for feed_id, items in items_dict.items():
            max_days = feed_max_age_map.get(feed_id)
            if max_days is None:
                max_days = default_max_age_days

            for item in items:
                if freshness_enabled and max_days > 0:
                    if item.published_at and not is_within_days(item.published_at, max_days, timezone):
                        filtered_count += 1
                        continue

                rss_items.append(
                    {
                        "title": item.title,
                        "feed_id": feed_id,
                        "feed_name": id_to_name.get(feed_id, feed_id),
                        "url": item.url,
                        "published_at": item.published_at,
                        "summary": item.summary,
                        "author": item.author,
                    }
                )

        if filtered_count > 0:
            print(f"[RSS] 新鲜度过滤：跳过 {filtered_count} 篇超过指定天数的旧文章（仍保留在数据库中）")

        return rss_items

    def _execute_mode_strategy(
        self,
        mode_strategy: Dict,
        results: Dict,
        id_to_name: Dict,
        failed_ids: List,
        rss_items: Optional[List[Dict]] = None,
        rss_new_items: Optional[List[Dict]] = None,
    ) -> Optional[str]:
        current_platform_ids = self.ctx.platform_ids

        # RSS-only：未配置任何热榜平台时，跳过历史 news 读取逻辑，直接生成 RSS 报告并发送通知
        if not current_platform_ids:
            word_groups, filter_words, global_filters = self.ctx.load_frequency_words()
            stats, html_file = self._run_analysis_pipeline(
                data_source={},
                mode=self.report_mode,
                title_info={},
                new_titles={},
                word_groups=word_groups,
                filter_words=filter_words,
                id_to_name={},
                failed_ids=[],
                is_daily_summary=True,
                global_filters=global_filters,
                rss_items=rss_items,
                rss_new_items=rss_new_items,
            )
            if html_file:
                print(f"HTML报告已生成: {html_file}")

            report_type = mode_strategy["realtime_report_type"] if mode_strategy.get("should_send_realtime") else mode_strategy.get("summary_report_type", "当日汇总")
            self._send_notification_if_needed(
                stats,
                report_type,
                self.report_mode,
                failed_ids=[],
                new_titles={},
                id_to_name={},
                html_file_path=html_file,
                rss_items=rss_items,
                rss_new_items=rss_new_items,
            )

            if self._should_open_browser() and html_file:
                file_url = "file://" + str(Path(html_file).resolve())
                print(f"正在打开HTML报告: {file_url}")
                webbrowser.open(file_url)
            return html_file

        new_titles = self.ctx.detect_new_titles(current_platform_ids)
        time_info = self.ctx.format_time()
        if self.ctx.config["STORAGE"]["FORMATS"]["TXT"]:
            self.ctx.save_titles(results, id_to_name, failed_ids)
        word_groups, filter_words, global_filters = self.ctx.load_frequency_words()

        if self.report_mode == "current":
            analysis_data = self._load_analysis_data()
            if analysis_data:
                (
                    all_results,
                    historical_id_to_name,
                    historical_title_info,
                    historical_new_titles,
                    _,
                    _,
                    _,
                ) = analysis_data

                print(f"current模式：使用过滤后的历史数据，包含平台：{list(all_results.keys())}")

                stats, html_file = self._run_analysis_pipeline(
                    all_results,
                    self.report_mode,
                    historical_title_info,
                    historical_new_titles,
                    word_groups,
                    filter_words,
                    historical_id_to_name,
                    failed_ids=failed_ids,
                    global_filters=global_filters,
                    rss_items=rss_items,
                    rss_new_items=rss_new_items,
                )

                combined_id_to_name = {**historical_id_to_name, **id_to_name}

                if html_file:
                    print(f"HTML报告已生成: {html_file}")

                if mode_strategy["should_send_realtime"]:
                    self._send_notification_if_needed(
                        stats,
                        mode_strategy["realtime_report_type"],
                        self.report_mode,
                        failed_ids=failed_ids,
                        new_titles=historical_new_titles,
                        id_to_name=combined_id_to_name,
                        html_file_path=html_file,
                        rss_items=rss_items,
                        rss_new_items=rss_new_items,
                    )
            else:
                print("❌ 严重错误：无法读取刚保存的数据文件")
                raise RuntimeError("数据一致性检查失败：保存后立即读取失败")
        else:
            title_info = self._prepare_current_title_info(results, time_info)
            stats, html_file = self._run_analysis_pipeline(
                results,
                self.report_mode,
                title_info,
                new_titles,
                word_groups,
                filter_words,
                id_to_name,
                failed_ids=failed_ids,
                global_filters=global_filters,
                rss_items=rss_items,
                rss_new_items=rss_new_items,
            )
            if html_file:
                print(f"HTML报告已生成: {html_file}")

            if mode_strategy["should_send_realtime"]:
                self._send_notification_if_needed(
                    stats,
                    mode_strategy["realtime_report_type"],
                    self.report_mode,
                    failed_ids=failed_ids,
                    new_titles=new_titles,
                    id_to_name=id_to_name,
                    html_file_path=html_file,
                    rss_items=rss_items,
                    rss_new_items=rss_new_items,
                )

        summary_html = None
        if mode_strategy["should_generate_summary"]:
            if mode_strategy["should_send_realtime"]:
                summary_html = self._generate_summary_html(
                    mode_strategy["summary_mode"],
                    rss_items=rss_items,
                    rss_new_items=rss_new_items,
                )
            else:
                summary_html = self._generate_summary_report(
                    mode_strategy, rss_items=rss_items, rss_new_items=rss_new_items
                )

        if self._should_open_browser() and html_file:
            if summary_html:
                summary_url = "file://" + str(Path(summary_html).resolve())
                print(f"正在打开汇总报告: {summary_url}")
                webbrowser.open(summary_url)
            else:
                file_url = "file://" + str(Path(html_file).resolve())
                print(f"正在打开HTML报告: {file_url}")
                webbrowser.open(file_url)
        elif self.is_docker_container and html_file:
            if summary_html:
                print(f"汇总报告已生成（Docker环境）: {summary_html}")
            else:
                print(f"HTML报告已生成（Docker环境）: {html_file}")

        return summary_html

    def run(self) -> None:
        try:
            self._initialize_and_check_config()
            mode_strategy = self._get_mode_strategy()

            results, id_to_name, failed_ids = self._crawl_data()
            rss_items, rss_new_items = self._crawl_rss_data()

            self._execute_mode_strategy(
                mode_strategy,
                results,
                id_to_name,
                failed_ids,
                rss_items=rss_items,
                rss_new_items=rss_new_items,
            )

        except Exception as e:
            print(f"分析流程执行出错: {e}")
            raise
        finally:
            self.ctx.cleanup()
