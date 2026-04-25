# coding=utf-8
"""
通知调度器模块

提供统一的通知分发接口。
支持所有通知渠道的多账号配置，使用 `;` 分隔多个账号。

使用示例:
    dispatcher = NotificationDispatcher(config, get_time_func, split_content_func)
    results = dispatcher.dispatch_all(report_data, report_type, ...)
"""

from typing import Any, Callable, Dict, List, Optional

from NewsPilot.core.config import (
    get_account_at_index,
    limit_accounts,
    parse_multi_account_config,
    validate_paired_configs,
)

from .senders import (
    send_to_bark,
    send_to_dingtalk,
    send_to_email,
    send_to_feishu,
    send_to_ntfy,
    send_to_slack,
    send_to_telegram,
    send_to_wework,
)
from .renderer import (
    render_rss_feishu_content,
    render_rss_dingtalk_content,
    render_rss_markdown_content,
)


class NotificationDispatcher:
    """
    统一的多账号通知调度器

    将多账号发送逻辑封装，提供简洁的 dispatch_all 接口。
    内部处理账号解析、数量限制、配对验证等逻辑。
    """

    def __init__(
        self,
        config: Dict[str, Any],
        get_time_func: Callable,
        split_content_func: Callable,
    ):
        """
        初始化通知调度器

        Args:
            config: 完整的配置字典，包含所有通知渠道的配置
            get_time_func: 获取当前时间的函数
            split_content_func: 内容分批函数
        """
        self.config = config
        self.get_time_func = get_time_func
        self.split_content_func = split_content_func
        self.max_accounts = config.get("MAX_ACCOUNTS_PER_CHANNEL", 3)

    def dispatch_all(
        self,
        report_data: Dict,
        report_type: str,
        update_info: Optional[Dict] = None,
        proxy_url: Optional[str] = None,
        mode: str = "daily",
        html_file_path: Optional[str] = None,
        rss_items: Optional[List[Dict]] = None,
        rss_new_items: Optional[List[Dict]] = None,
    ) -> Dict[str, bool]:
        """
        分发通知到所有已配置的渠道（支持热榜+RSS合并推送）

        Args:
            report_data: 报告数据（由 prepare_report_data 生成）
            report_type: 报告类型（如 "当日汇总"、"实时增量"）
            update_info: 版本更新信息（可选）
            proxy_url: 代理 URL（可选）
            mode: 报告模式 (daily/current/incremental)
            html_file_path: HTML 报告文件路径（邮件使用）
            rss_items: RSS 统计条目列表（用于 RSS 统计区块）
            rss_new_items: RSS 新增条目列表（用于 RSS 新增区块）

        Returns:
            Dict[str, bool]: 每个渠道的发送结果，key 为渠道名，value 为是否成功
        """
        results = {}

        # 飞书
        if self.config.get("FEISHU_WEBHOOK_URL"):
            results["feishu"] = self._send_feishu(
                report_data, report_type, update_info, proxy_url, mode, rss_items, rss_new_items
            )

        # 钉钉
        if self.config.get("DINGTALK_WEBHOOK_URL"):
            results["dingtalk"] = self._send_dingtalk(
                report_data, report_type, update_info, proxy_url, mode, rss_items, rss_new_items
            )

        # 企业微信
        if self.config.get("WEWORK_WEBHOOK_URL"):
            results["wework"] = self._send_wework(
                report_data, report_type, update_info, proxy_url, mode, rss_items, rss_new_items
            )

        # Telegram（需要配对验证）
        if self.config.get("TELEGRAM_BOT_TOKEN") and self.config.get("TELEGRAM_CHAT_ID"):
            results["telegram"] = self._send_telegram(
                report_data, report_type, update_info, proxy_url, mode, rss_items, rss_new_items
            )

        # ntfy（需要配对验证）
        if self.config.get("NTFY_SERVER_URL") and self.config.get("NTFY_TOPIC"):
            results["ntfy"] = self._send_ntfy(
                report_data, report_type, update_info, proxy_url, mode, rss_items, rss_new_items
            )

        # Bark
        if self.config.get("BARK_URL"):
            results["bark"] = self._send_bark(
                report_data, report_type, update_info, proxy_url, mode, rss_items, rss_new_items
            )

        # Slack
        if self.config.get("SLACK_WEBHOOK_URL"):
            results["slack"] = self._send_slack(
                report_data, report_type, update_info, proxy_url, mode, rss_items, rss_new_items
            )

        # 邮件（保持原有逻辑，已支持多收件人）
        if (
            self.config.get("EMAIL_FROM")
            and self.config.get("EMAIL_PASSWORD")
            and self.config.get("EMAIL_TO")
        ):
            results["email"] = self._send_email(report_type, html_file_path)

        return results

    def _send_to_multi_accounts(
        self,
        channel_name: str,
        config_value: str,
        send_func: Callable[..., bool],
        **kwargs,
    ) -> bool:
        """
        通用多账号发送逻辑

        Args:
            channel_name: 渠道名称（用于日志和账号数量限制提示）
            config_value: 配置值（可能包含多个账号，用 ; 分隔）
            send_func: 发送函数，签名为 (account, account_label=..., **kwargs) -> bool
            **kwargs: 传递给发送函数的其他参数

        Returns:
            bool: 任一账号发送成功则返回 True
        """
        accounts = parse_multi_account_config(config_value)
        if not accounts:
            return False

        accounts = limit_accounts(accounts, self.max_accounts, channel_name)
        results = []

        for i, account in enumerate(accounts):
            if account:
                account_label = f"账号{i+1}" if len(accounts) > 1 else ""
                result = send_func(account, account_label=account_label, **kwargs)
                results.append(result)

        return any(results) if results else False

    def _send_feishu(
        self,
        report_data: Dict,
        report_type: str,
        update_info: Optional[Dict],
        proxy_url: Optional[str],
        mode: str,
        rss_items: Optional[List[Dict]] = None,
        rss_new_items: Optional[List[Dict]] = None,
    ) -> bool:
        """发送到飞书（多账号，支持热榜+RSS合并）"""
        return self._send_to_multi_accounts(
            channel_name="飞书",
            config_value=self.config["FEISHU_WEBHOOK_URL"],
            send_func=lambda url, account_label: send_to_feishu(
                webhook_url=url,
                report_data=report_data,
                report_type=report_type,
                update_info=update_info,
                proxy_url=proxy_url,
                mode=mode,
                account_label=account_label,
                batch_size=self.config.get("FEISHU_BATCH_SIZE", 29000),
                batch_interval=self.config.get("BATCH_SEND_INTERVAL", 1.0),
                split_content_func=self.split_content_func,
                get_time_func=self.get_time_func,
                rss_items=rss_items,
                rss_new_items=rss_new_items,
            ),
        )

    def _send_dingtalk(
        self,
        report_data: Dict,
        report_type: str,
        update_info: Optional[Dict],
        proxy_url: Optional[str],
        mode: str,
        rss_items: Optional[List[Dict]] = None,
        rss_new_items: Optional[List[Dict]] = None,
    ) -> bool:
        """发送到钉钉（多账号，支持热榜+RSS合并）"""
        return self._send_to_multi_accounts(
            channel_name="钉钉",
            config_value=self.config["DINGTALK_WEBHOOK_URL"],
            send_func=lambda url, account_label: send_to_dingtalk(
                webhook_url=url,
                report_data=report_data,
                report_type=report_type,
                update_info=update_info,
                proxy_url=proxy_url,
                mode=mode,
                account_label=account_label,
                batch_size=self.config.get("DINGTALK_BATCH_SIZE", 20000),
                batch_interval=self.config.get("BATCH_SEND_INTERVAL", 1.0),
                split_content_func=self.split_content_func,
                rss_items=rss_items,
                rss_new_items=rss_new_items,
            ),
        )

    def _send_wework(
        self,
        report_data: Dict,
        report_type: str,
        update_info: Optional[Dict],
        proxy_url: Optional[str],
        mode: str,
        rss_items: Optional[List[Dict]] = None,
        rss_new_items: Optional[List[Dict]] = None,
    ) -> bool:
        """发送到企业微信（多账号，支持热榜+RSS合并）"""
        return self._send_to_multi_accounts(
            channel_name="企业微信",
            config_value=self.config["WEWORK_WEBHOOK_URL"],
            send_func=lambda url, account_label: send_to_wework(
                webhook_url=url,
                report_data=report_data,
                report_type=report_type,
                update_info=update_info,
                proxy_url=proxy_url,
                mode=mode,
                account_label=account_label,
                batch_size=self.config.get("MESSAGE_BATCH_SIZE", 4000),
                batch_interval=self.config.get("BATCH_SEND_INTERVAL", 1.0),
                msg_type=self.config.get("WEWORK_MSG_TYPE", "markdown"),
                split_content_func=self.split_content_func,
                rss_items=rss_items,
                rss_new_items=rss_new_items,
            ),
        )

    def _send_telegram(
        self,
        report_data: Dict,
        report_type: str,
        update_info: Optional[Dict],
        proxy_url: Optional[str],
        mode: str,
        rss_items: Optional[List[Dict]] = None,
        rss_new_items: Optional[List[Dict]] = None,
    ) -> bool:
        """发送到 Telegram（多账号，需验证 token 和 chat_id 配对，支持热榜+RSS合并）"""
        telegram_tokens = parse_multi_account_config(self.config["TELEGRAM_BOT_TOKEN"])
        telegram_chat_ids = parse_multi_account_config(self.config["TELEGRAM_CHAT_ID"])

        if not telegram_tokens or not telegram_chat_ids:
            return False

        # 验证配对
        valid, count = validate_paired_configs(
            {"bot_token": telegram_tokens, "chat_id": telegram_chat_ids},
            "Telegram",
            required_keys=["bot_token", "chat_id"],
        )
        if not valid or count == 0:
            return False

        # 限制账号数量
        telegram_tokens = limit_accounts(telegram_tokens, self.max_accounts, "Telegram")
        telegram_chat_ids = telegram_chat_ids[: len(telegram_tokens)]

        results = []
        for i in range(len(telegram_tokens)):
            token = telegram_tokens[i]
            chat_id = telegram_chat_ids[i]
            if token and chat_id:
                account_label = f"账号{i+1}" if len(telegram_tokens) > 1 else ""
                result = send_to_telegram(
                    bot_token=token,
                    chat_id=chat_id,
                    report_data=report_data,
                    report_type=report_type,
                    update_info=update_info,
                    proxy_url=proxy_url,
                    mode=mode,
                    account_label=account_label,
                    batch_size=self.config.get("MESSAGE_BATCH_SIZE", 4000),
                    batch_interval=self.config.get("BATCH_SEND_INTERVAL", 1.0),
                    split_content_func=self.split_content_func,
                    rss_items=rss_items,
                    rss_new_items=rss_new_items,
                )
                results.append(result)

        return any(results) if results else False

    def _send_ntfy(
        self,
        report_data: Dict,
        report_type: str,
        update_info: Optional[Dict],
        proxy_url: Optional[str],
        mode: str,
        rss_items: Optional[List[Dict]] = None,
        rss_new_items: Optional[List[Dict]] = None,
    ) -> bool:
        """发送到 ntfy（多账号，需验证 topic 和 token 配对，支持热榜+RSS合并）"""
        ntfy_server_url = self.config["NTFY_SERVER_URL"]
        ntfy_topics = parse_multi_account_config(self.config["NTFY_TOPIC"])
        ntfy_tokens = parse_multi_account_config(self.config.get("NTFY_TOKEN", ""))

        if not ntfy_server_url or not ntfy_topics:
            return False

        # 验证 token 和 topic 数量一致（如果配置了 token）
        if ntfy_tokens and len(ntfy_tokens) != len(ntfy_topics):
            print(
                f"❌ ntfy 配置错误：topic 数量({len(ntfy_topics)})与 token 数量({len(ntfy_tokens)})不一致，跳过 ntfy 推送"
            )
            return False

        # 限制账号数量
        ntfy_topics = limit_accounts(ntfy_topics, self.max_accounts, "ntfy")
        if ntfy_tokens:
            ntfy_tokens = ntfy_tokens[: len(ntfy_topics)]

        results = []
        for i, topic in enumerate(ntfy_topics):
            if topic:
                token = get_account_at_index(ntfy_tokens, i, "") if ntfy_tokens else ""
                account_label = f"账号{i+1}" if len(ntfy_topics) > 1 else ""
                result = send_to_ntfy(
                    server_url=ntfy_server_url,
                    topic=topic,
                    token=token,
                    report_data=report_data,
                    report_type=report_type,
                    update_info=update_info,
                    proxy_url=proxy_url,
                    mode=mode,
                    account_label=account_label,
                    batch_size=3800,
                    split_content_func=self.split_content_func,
                    rss_items=rss_items,
                    rss_new_items=rss_new_items,
                )
                results.append(result)

        return any(results) if results else False

    def _send_bark(
        self,
        report_data: Dict,
        report_type: str,
        update_info: Optional[Dict],
        proxy_url: Optional[str],
        mode: str,
        rss_items: Optional[List[Dict]] = None,
        rss_new_items: Optional[List[Dict]] = None,
    ) -> bool:
        """发送到 Bark（多账号，支持热榜+RSS合并）"""
        return self._send_to_multi_accounts(
            channel_name="Bark",
            config_value=self.config["BARK_URL"],
            send_func=lambda url, account_label: send_to_bark(
                bark_url=url,
                report_data=report_data,
                report_type=report_type,
                update_info=update_info,
                proxy_url=proxy_url,
                mode=mode,
                account_label=account_label,
                batch_size=self.config.get("BARK_BATCH_SIZE", 3600),
                batch_interval=self.config.get("BATCH_SEND_INTERVAL", 1.0),
                split_content_func=self.split_content_func,
                rss_items=rss_items,
                rss_new_items=rss_new_items,
            ),
        )

    def _send_slack(
        self,
        report_data: Dict,
        report_type: str,
        update_info: Optional[Dict],
        proxy_url: Optional[str],
        mode: str,
        rss_items: Optional[List[Dict]] = None,
        rss_new_items: Optional[List[Dict]] = None,
    ) -> bool:
        """发送到 Slack（多账号，支持热榜+RSS合并）"""
        return self._send_to_multi_accounts(
            channel_name="Slack",
            config_value=self.config["SLACK_WEBHOOK_URL"],
            send_func=lambda url, account_label: send_to_slack(
                webhook_url=url,
                report_data=report_data,
                report_type=report_type,
                update_info=update_info,
                proxy_url=proxy_url,
                mode=mode,
                account_label=account_label,
                batch_size=self.config.get("SLACK_BATCH_SIZE", 4000),
                batch_interval=self.config.get("BATCH_SEND_INTERVAL", 1.0),
                split_content_func=self.split_content_func,
                rss_items=rss_items,
                rss_new_items=rss_new_items,
            ),
        )

    def _send_email(
        self,
        report_type: str,
        html_file_path: Optional[str],
    ) -> bool:
        """发送邮件（保持原有逻辑，已支持多收件人）"""
        return send_to_email(
            from_email=self.config["EMAIL_FROM"],
            password=self.config["EMAIL_PASSWORD"],
            to_email=self.config["EMAIL_TO"],
            report_type=report_type,
            html_file_path=html_file_path,
            custom_smtp_server=self.config.get("EMAIL_SMTP_SERVER", ""),
            custom_smtp_port=self.config.get("EMAIL_SMTP_PORT", ""),
            get_time_func=self.get_time_func,
        )

    # === RSS 通知方法 ===

    def dispatch_rss(
        self,
        rss_items: List[Dict],
        feeds_info: Optional[Dict[str, str]] = None,
        proxy_url: Optional[str] = None,
        html_file_path: Optional[str] = None,
    ) -> Dict[str, bool]:
        """
        分发 RSS 通知到所有已配置的渠道

        Args:
            rss_items: RSS 条目列表，每个条目包含:
                - title: 标题
                - feed_id: RSS 源 ID
                - feed_name: RSS 源名称
                - url: 链接
                - published_at: 发布时间
                - summary: 摘要（可选）
                - author: 作者（可选）
            feeds_info: RSS 源 ID 到名称的映射
            proxy_url: 代理 URL（可选）
            html_file_path: HTML 报告文件路径（邮件使用）

        Returns:
            Dict[str, bool]: 每个渠道的发送结果
        """
        if not rss_items:
            print("[RSS通知] 没有 RSS 内容，跳过通知")
            return {}

        results = {}
        report_type = "RSS 订阅更新"

        # 飞书
        if self.config.get("FEISHU_WEBHOOK_URL"):
            results["feishu"] = self._send_rss_feishu(
                rss_items, feeds_info, proxy_url
            )

        # 钉钉
        if self.config.get("DINGTALK_WEBHOOK_URL"):
            results["dingtalk"] = self._send_rss_dingtalk(
                rss_items, feeds_info, proxy_url
            )

        # 企业微信
        if self.config.get("WEWORK_WEBHOOK_URL"):
            results["wework"] = self._send_rss_markdown(
                rss_items, feeds_info, proxy_url, "wework"
            )

        # Telegram
        if self.config.get("TELEGRAM_BOT_TOKEN") and self.config.get("TELEGRAM_CHAT_ID"):
            results["telegram"] = self._send_rss_markdown(
                rss_items, feeds_info, proxy_url, "telegram"
            )

        # ntfy
        if self.config.get("NTFY_SERVER_URL") and self.config.get("NTFY_TOPIC"):
            results["ntfy"] = self._send_rss_markdown(
                rss_items, feeds_info, proxy_url, "ntfy"
            )

        # Bark
        if self.config.get("BARK_URL"):
            results["bark"] = self._send_rss_markdown(
                rss_items, feeds_info, proxy_url, "bark"
            )

        # Slack
        if self.config.get("SLACK_WEBHOOK_URL"):
            results["slack"] = self._send_rss_markdown(
                rss_items, feeds_info, proxy_url, "slack"
            )

        # 邮件
        if (
            self.config.get("EMAIL_FROM")
            and self.config.get("EMAIL_PASSWORD")
            and self.config.get("EMAIL_TO")
        ):
            results["email"] = self._send_email(report_type, html_file_path)

        return results

    def _send_rss_feishu(
        self,
        rss_items: List[Dict],
        feeds_info: Optional[Dict[str, str]],
        proxy_url: Optional[str],
    ) -> bool:
        """发送 RSS 到飞书"""
        import requests

        content = render_rss_feishu_content(
            rss_items=rss_items,
            feeds_info=feeds_info,
            get_time_func=self.get_time_func,
        )

        webhooks = parse_multi_account_config(self.config["FEISHU_WEBHOOK_URL"])
        webhooks = limit_accounts(webhooks, self.max_accounts, "飞书")

        results = []
        for i, webhook_url in enumerate(webhooks):
            if not webhook_url:
                continue

            account_label = f"账号{i+1}" if len(webhooks) > 1 else ""
            try:
                # 分批发送
                batches = self.split_content_func(
                    content, self.config.get("FEISHU_BATCH_SIZE", 29000)
                )

                for batch_idx, batch_content in enumerate(batches):
                    payload = {
                        "msg_type": "interactive",
                        "card": {
                            "header": {
                                "title": {
                                    "tag": "plain_text",
                                    "content": f"📰 RSS 订阅更新 {f'({batch_idx + 1}/{len(batches)})' if len(batches) > 1 else ''}",
                                },
                                "template": "green",
                            },
                            "elements": [
                                {"tag": "markdown", "content": batch_content}
                            ],
                        },
                    }

                    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
                    resp = requests.post(webhook_url, json=payload, proxies=proxies, timeout=30)
                    resp.raise_for_status()

                print(f"✅ 飞书{account_label} RSS 通知发送成功")
                results.append(True)
            except Exception as e:
                print(f"❌ 飞书{account_label} RSS 通知发送失败: {e}")
                results.append(False)

        return any(results) if results else False

    def _send_rss_dingtalk(
        self,
        rss_items: List[Dict],
        feeds_info: Optional[Dict[str, str]],
        proxy_url: Optional[str],
    ) -> bool:
        """发送 RSS 到钉钉"""
        import requests

        content = render_rss_dingtalk_content(
            rss_items=rss_items,
            feeds_info=feeds_info,
            get_time_func=self.get_time_func,
        )

        webhooks = parse_multi_account_config(self.config["DINGTALK_WEBHOOK_URL"])
        webhooks = limit_accounts(webhooks, self.max_accounts, "钉钉")

        results = []
        for i, webhook_url in enumerate(webhooks):
            if not webhook_url:
                continue

            account_label = f"账号{i+1}" if len(webhooks) > 1 else ""
            try:
                batches = self.split_content_func(
                    content, self.config.get("DINGTALK_BATCH_SIZE", 20000)
                )

                for batch_idx, batch_content in enumerate(batches):
                    title = f"📰 RSS 订阅更新 {f'({batch_idx + 1}/{len(batches)})' if len(batches) > 1 else ''}"
                    payload = {
                        "msgtype": "markdown",
                        "markdown": {
                            "title": title,
                            "text": batch_content,
                        },
                    }

                    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
                    resp = requests.post(webhook_url, json=payload, proxies=proxies, timeout=30)
                    resp.raise_for_status()

                print(f"✅ 钉钉{account_label} RSS 通知发送成功")
                results.append(True)
            except Exception as e:
                print(f"❌ 钉钉{account_label} RSS 通知发送失败: {e}")
                results.append(False)

        return any(results) if results else False

    def _send_rss_markdown(
        self,
        rss_items: List[Dict],
        feeds_info: Optional[Dict[str, str]],
        proxy_url: Optional[str],
        channel: str,
    ) -> bool:
        """发送 RSS 到 Markdown 兼容渠道（企业微信、Telegram、ntfy、Bark、Slack）"""
        import requests

        content = render_rss_markdown_content(
            rss_items=rss_items,
            feeds_info=feeds_info,
            get_time_func=self.get_time_func,
        )

        try:
            if channel == "wework":
                return self._send_rss_wework(content, proxy_url)
            elif channel == "telegram":
                return self._send_rss_telegram(content, proxy_url)
            elif channel == "ntfy":
                return self._send_rss_ntfy(content, proxy_url)
            elif channel == "bark":
                return self._send_rss_bark(content, proxy_url)
            elif channel == "slack":
                return self._send_rss_slack(content, proxy_url)
        except Exception as e:
            print(f"❌ {channel} RSS 通知发送失败: {e}")
            return False

        return False

    def _send_rss_wework(self, content: str, proxy_url: Optional[str]) -> bool:
        """发送 RSS 到企业微信"""
        import requests

        webhooks = parse_multi_account_config(self.config["WEWORK_WEBHOOK_URL"])
        webhooks = limit_accounts(webhooks, self.max_accounts, "企业微信")

        results = []
        for i, webhook_url in enumerate(webhooks):
            if not webhook_url:
                continue

            account_label = f"账号{i+1}" if len(webhooks) > 1 else ""
            try:
                batches = self.split_content_func(
                    content, self.config.get("MESSAGE_BATCH_SIZE", 4000)
                )

                for batch_content in batches:
                    payload = {
                        "msgtype": "markdown",
                        "markdown": {"content": batch_content},
                    }

                    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
                    resp = requests.post(webhook_url, json=payload, proxies=proxies, timeout=30)
                    resp.raise_for_status()

                print(f"✅ 企业微信{account_label} RSS 通知发送成功")
                results.append(True)
            except Exception as e:
                print(f"❌ 企业微信{account_label} RSS 通知发送失败: {e}")
                results.append(False)

        return any(results) if results else False

    def _send_rss_telegram(self, content: str, proxy_url: Optional[str]) -> bool:
        """发送 RSS 到 Telegram"""
        import requests

        tokens = parse_multi_account_config(self.config["TELEGRAM_BOT_TOKEN"])
        chat_ids = parse_multi_account_config(self.config["TELEGRAM_CHAT_ID"])

        if not tokens or not chat_ids:
            return False

        results = []
        for i in range(min(len(tokens), len(chat_ids), self.max_accounts)):
            token = tokens[i]
            chat_id = chat_ids[i]

            if not token or not chat_id:
                continue

            account_label = f"账号{i+1}" if len(tokens) > 1 else ""
            try:
                batches = self.split_content_func(
                    content, self.config.get("MESSAGE_BATCH_SIZE", 4000)
                )

                for batch_content in batches:
                    url = f"https://api.telegram.org/bot{token}/sendMessage"
                    payload = {
                        "chat_id": chat_id,
                        "text": batch_content,
                        "parse_mode": "Markdown",
                    }

                    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
                    resp = requests.post(url, json=payload, proxies=proxies, timeout=30)
                    resp.raise_for_status()

                print(f"✅ Telegram{account_label} RSS 通知发送成功")
                results.append(True)
            except Exception as e:
                print(f"❌ Telegram{account_label} RSS 通知发送失败: {e}")
                results.append(False)

        return any(results) if results else False

    def _send_rss_ntfy(self, content: str, proxy_url: Optional[str]) -> bool:
        """发送 RSS 到 ntfy"""
        import requests

        server_url = self.config["NTFY_SERVER_URL"]
        topics = parse_multi_account_config(self.config["NTFY_TOPIC"])
        tokens = parse_multi_account_config(self.config.get("NTFY_TOKEN", ""))

        if not server_url or not topics:
            return False

        topics = limit_accounts(topics, self.max_accounts, "ntfy")

        results = []
        for i, topic in enumerate(topics):
            if not topic:
                continue

            token = tokens[i] if tokens and i < len(tokens) else ""
            account_label = f"账号{i+1}" if len(topics) > 1 else ""

            try:
                batches = self.split_content_func(content, 3800)

                for batch_content in batches:
                    url = f"{server_url.rstrip('/')}/{topic}"
                    headers = {"Title": "RSS 订阅更新", "Markdown": "yes"}
                    if token:
                        headers["Authorization"] = f"Bearer {token}"

                    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
                    resp = requests.post(
                        url, data=batch_content.encode("utf-8"),
                        headers=headers, proxies=proxies, timeout=30
                    )
                    resp.raise_for_status()

                print(f"✅ ntfy{account_label} RSS 通知发送成功")
                results.append(True)
            except Exception as e:
                print(f"❌ ntfy{account_label} RSS 通知发送失败: {e}")
                results.append(False)

        return any(results) if results else False

    def _send_rss_bark(self, content: str, proxy_url: Optional[str]) -> bool:
        """发送 RSS 到 Bark"""
        import requests
        import urllib.parse

        urls = parse_multi_account_config(self.config["BARK_URL"])
        urls = limit_accounts(urls, self.max_accounts, "Bark")

        results = []
        for i, bark_url in enumerate(urls):
            if not bark_url:
                continue

            account_label = f"账号{i+1}" if len(urls) > 1 else ""
            try:
                batches = self.split_content_func(
                    content, self.config.get("BARK_BATCH_SIZE", 3600)
                )

                for batch_content in batches:
                    title = urllib.parse.quote("📰 RSS 订阅更新")
                    body = urllib.parse.quote(batch_content)
                    url = f"{bark_url.rstrip('/')}/{title}/{body}"

                    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
                    resp = requests.get(url, proxies=proxies, timeout=30)
                    resp.raise_for_status()

                print(f"✅ Bark{account_label} RSS 通知发送成功")
                results.append(True)
            except Exception as e:
                print(f"❌ Bark{account_label} RSS 通知发送失败: {e}")
                results.append(False)

        return any(results) if results else False

    def _send_rss_slack(self, content: str, proxy_url: Optional[str]) -> bool:
        """发送 RSS 到 Slack"""
        import requests

        webhooks = parse_multi_account_config(self.config["SLACK_WEBHOOK_URL"])
        webhooks = limit_accounts(webhooks, self.max_accounts, "Slack")

        results = []
        for i, webhook_url in enumerate(webhooks):
            if not webhook_url:
                continue

            account_label = f"账号{i+1}" if len(webhooks) > 1 else ""
            try:
                batches = self.split_content_func(
                    content, self.config.get("SLACK_BATCH_SIZE", 4000)
                )

                for batch_content in batches:
                    payload = {
                        "blocks": [
                            {
                                "type": "section",
                                "text": {
                                    "type": "mrkdwn",
                                    "text": batch_content,
                                },
                            }
                        ]
                    }

                    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
                    resp = requests.post(webhook_url, json=payload, proxies=proxies, timeout=30)
                    resp.raise_for_status()

                print(f"✅ Slack{account_label} RSS 通知发送成功")
                results.append(True)
            except Exception as e:
                print(f"❌ Slack{account_label} RSS 通知发送失败: {e}")
                results.append(False)

        return any(results) if results else False
