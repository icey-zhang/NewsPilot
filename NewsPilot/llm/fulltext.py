# coding=utf-8
from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Optional, Dict

import requests


@dataclass(frozen=True)
class FullTextConfig:
    enabled: bool = False
    timeout: int = 15
    max_bytes: int = 1_200_000  # ~1.2MB
    max_chars: int = 6000       # per-article text cap
    min_paragraph_chars: int = 60
    request_interval_ms: int = 200
    use_proxy: bool = False
    proxy_url: str = ""


def fetch_article_text(url: str, cfg: FullTextConfig) -> Optional[str]:
    """
    Fetch and extract readable text from an article URL.

    Best-effort heuristic (no extra deps): strip scripts/styles, extract text, keep longer paragraphs.
    """
    if not cfg.enabled:
        return None
    if not url:
        return None

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
    }

    proxies: Optional[Dict[str, str]] = None
    if cfg.use_proxy and cfg.proxy_url:
        proxies = {"http": cfg.proxy_url, "https": cfg.proxy_url}

    try:
        resp = requests.get(url, headers=headers, timeout=cfg.timeout, proxies=proxies, stream=True)
        resp.raise_for_status()

        content_type = (resp.headers.get("Content-Type") or "").lower()
        if "text/html" not in content_type and "application/xhtml" not in content_type and "xml" not in content_type:
            # still try: some sites mislabel
            pass

        raw = _read_limited(resp, cfg.max_bytes)
        if not raw:
            return None

        # requests may not know correct encoding; best-effort
        encoding = resp.encoding or "utf-8"
        try:
            html = raw.decode(encoding, errors="replace")
        except Exception:
            html = raw.decode("utf-8", errors="replace")

        # WeChat articles: prefer extracting from #js_content to avoid scripts/UI chrome.
        if "mp.weixin.qq.com" in url or 'id="js_content"' in html or "id='js_content'" in html:
            wechat_text = _extract_wechat_js_content(html)
            if wechat_text:
                wechat_text = _clean_extracted_text(wechat_text)
                wechat_text = _select_main_text(
                    wechat_text,
                    max_chars=cfg.max_chars,
                    min_paragraph_chars=cfg.min_paragraph_chars,
                )
                return wechat_text if _is_good_article_text(wechat_text) else None
            # Important: if we can't locate js_content, do NOT fallback to generic extraction.
            # WeChat pages frequently include large script/UI content which pollutes prompt.
            if "mp.weixin.qq.com" in url:
                return None

        text = _extract_text_from_html(html)
        if not text:
            return None

        text = _clean_extracted_text(text)

        text = _select_main_text(
            text,
            max_chars=cfg.max_chars,
            min_paragraph_chars=cfg.min_paragraph_chars,
        )
        return text if _is_good_article_text(text) else None
    except Exception:
        return None


def _read_limited(resp: requests.Response, max_bytes: int) -> bytes:
    data = bytearray()
    for chunk in resp.iter_content(chunk_size=65536):
        if not chunk:
            continue
        data.extend(chunk)
        if len(data) >= max_bytes:
            break
    return bytes(data)


_RE_SCRIPT = re.compile(r"(?is)<script[^>]*>.*?</script>")
_RE_STYLE = re.compile(r"(?is)<style[^>]*>.*?</style>")
_RE_NOSCRIPT = re.compile(r"(?is)<noscript[^>]*>.*?</noscript>")
_RE_TAG = re.compile(r"(?is)<[^>]+>")
_RE_BR = re.compile(r"(?i)<br\\s*/?>")
_RE_P_CLOSE = re.compile(r"(?i)</p\\s*>")
_RE_BLOCK_CLOSE = re.compile(r"(?i)</(div|section|article|h\\d|li)\\s*>")


def _extract_text_from_html(html: str) -> str:
    if not html:
        return ""

    cleaned = _RE_SCRIPT.sub(" ", html)
    cleaned = _RE_STYLE.sub(" ", cleaned)
    cleaned = _RE_NOSCRIPT.sub(" ", cleaned)

    # add newlines for some block boundaries to preserve paragraphs
    cleaned = _RE_BR.sub("\n", cleaned)
    cleaned = _RE_P_CLOSE.sub("\n", cleaned)
    cleaned = _RE_BLOCK_CLOSE.sub("\n", cleaned)

    cleaned = _RE_TAG.sub(" ", cleaned)
    cleaned = html_lib.unescape(cleaned)

    # normalize whitespace
    cleaned = cleaned.replace("\r", "\n")
    cleaned = re.sub(r"[ \\t\\f\\v]+", " ", cleaned)
    cleaned = re.sub(r"\\n{3,}", "\n\n", cleaned)
    cleaned = cleaned.strip()
    return cleaned


def _select_main_text(text: str, *, max_chars: int, min_paragraph_chars: int) -> str:
    paragraphs = [p.strip() for p in re.split(r"\\n\\s*\\n", text) if p.strip()]
    if not paragraphs:
        return text[:max_chars]

    # prefer longer paragraphs; keep order by selecting top blocks then re-sort by original order
    scored = []
    for idx, p in enumerate(paragraphs):
        # filter out navigation-like short lines
        score = len(p)
        scored.append((score, idx, p))

    # take candidates above threshold; fallback to top-N
    candidates = [x for x in scored if x[0] >= min_paragraph_chars]
    if not candidates:
        candidates = sorted(scored, reverse=True)[:10]
    else:
        candidates = sorted(candidates, reverse=True)[:20]

    candidates.sort(key=lambda x: x[1])
    out = "\n\n".join(p for _s, _i, p in candidates)
    out = out.strip()
    if len(out) > max_chars:
        out = out[:max_chars].rstrip()
    return out


class _WeChatContentParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._in_target = False
        self._target_tag = ""
        self._target_depth = 0
        self._in_ignored = False
        self._parts = []

    def handle_starttag(self, tag, attrs):
        tag_l = tag.lower()
        if tag_l in {"script", "style", "noscript"}:
            self._in_ignored = True
            return

        if not self._in_target:
            for k, v in attrs:
                if (k or "").lower() == "id" and (v or "") == "js_content":
                    self._in_target = True
                    self._target_tag = tag_l
                    self._target_depth = 1
                    return
        else:
            if tag_l == self._target_tag:
                self._target_depth += 1

            if tag_l in {"p", "br", "section", "h1", "h2", "h3", "h4", "li"}:
                self._parts.append("\n")

    def handle_endtag(self, tag):
        tag_l = tag.lower()
        if tag_l in {"script", "style", "noscript"}:
            self._in_ignored = False
            return

        if self._in_target:
            if tag_l == self._target_tag:
                self._target_depth -= 1
                if self._target_depth <= 0:
                    self._in_target = False
                    self._target_tag = ""
                    self._target_depth = 0
                    return

            if tag_l in {"p", "li"}:
                self._parts.append("\n")

    def handle_data(self, data):
        if not self._in_target or self._in_ignored:
            return
        s = (data or "").strip()
        if not s:
            return
        self._parts.append(s)
        self._parts.append(" ")

    def get_text(self) -> str:
        return "".join(self._parts)


def _extract_wechat_js_content(html: str) -> str:
    """
    Extract article body from WeChat official-account pages by parsing the #js_content element.
    """
    try:
        parser = _WeChatContentParser()
        parser.feed(html)
        text = parser.get_text()
        text = html_lib.unescape(text)
        text = text.replace("\r", "\n")
        text = re.sub(r"[ \t\f\v]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
    except Exception:
        return ""


def _is_good_article_text(text: str) -> bool:
    """
    Quality gate to avoid passing script/UI noise as 'content'.

    Heuristics:
    - must contain enough CJK chars (article body is mostly Chinese for our use-case)
    - must not be dominated by code-like lines
    """
    if not text:
        return False

    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    if cjk < 120:
        return False

    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if not lines:
        return False

    code_like = sum(1 for ln in lines if _is_code_like_line(ln))
    # If too many lines are code-like, it's not real body text
    if code_like / max(len(lines), 1) >= 0.15:
        return False

    return True


_BOILERPLATE_LINE_PATTERNS = [
    # 阅读器/导流
    r"在.*(小说阅读器|阅读器).*(沉浸阅读|阅读)",
    r"点击.*(查看|阅读全文|阅读原文)",
    r"(阅读原文|阅读全文|展开全文)$",
    r"扫描.*二维码",
    r"长按.*二维码",
    r"关注.*公众号",
    r"点个在看",
    # 推荐/相关阅读
    r"相关阅读",
    r"推荐阅读",
    r"更多精彩",
    # 常见页脚/导航
    r"(免责声明|版权声明|隐私政策|用户协议)",
    # 微信文章页常见 UI/提示
    r"微信扫一扫.*(关注该公众号|使用小程序|可打开此内容|完整服务)",
    r"(继续滑动看下一个|向上滑动看下一个|轻触阅读原文|知道了)$",
    r"(视频|小程序|赞|在看|分享|留言|收藏|听过)$",
    r"×$",
    r"分析$",
]
_BOILERPLATE_LINE_RE = re.compile(r"^(?:" + "|".join(_BOILERPLATE_LINE_PATTERNS) + r")$", re.I)

_CODE_KEYWORDS = [
    "window",
    "document",
    "getelementbyid",
    "classlist",
    "localstorage",
    "promise",
    "symbol",
    "use strict",
    "__inline_script__",
    # bundler/runtime hints
    "babelhelpers",
    "regeneratorruntime",
    "webpack",
    # runtime / logging / parsing
    "console",
    "jsonparse",
    "stringify",
    "gtag",
    "datalayer",
]


def _clean_extracted_text(text: str) -> str:
    """
    Clean extracted plain text before main-text selection.

    Goals:
    - collapse excessive blank lines
    - drop boilerplate navigation/CTA lines
    - drop repeated short site-name lines (e.g. '新智元' repeated many times)
    """
    if not text:
        return ""

    # Normalize whitespace first
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    lines = [ln.strip() for ln in text.split("\n")]
    cleaned_lines = []
    short_line_counts: Dict[str, int] = {}

    for ln in lines:
        if not ln:
            cleaned_lines.append("")
            continue

        # Drop punctuation-only noise lines
        if re.fullmatch(r"[:：。．·•,，;；!！?？\\-—_]+", ln):
            continue

        # Drop boilerplate lines
        if _BOILERPLATE_LINE_RE.match(ln):
            continue

        # Drop code-like lines (common on JS-rendered or embedded-script pages)
        if _is_code_like_line(ln):
            continue

        # Count short lines to remove heavy repetition (e.g. repeated site name)
        if len(ln) <= 6:
            short_line_counts[ln] = short_line_counts.get(ln, 0) + 1

        cleaned_lines.append(ln)

    # Second pass: drop overly repeated short lines
    out_lines = []
    for ln in cleaned_lines:
        if ln and len(ln) <= 6 and short_line_counts.get(ln, 0) >= 3:
            continue
        out_lines.append(ln)

    out = "\n".join(out_lines)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out


def _is_code_like_line(line: str) -> bool:
    """
    Heuristic to drop JS/code fragments that leak into extracted text.

    Some sites (notably WeChat) may inject scripts where tag-stripping leaves mangled code text.
    """
    if not line:
        return False

    # If the line has almost no CJK characters but contains code punctuation, it's likely script noise.
    cjk = sum(1 for ch in line if "\u4e00" <= ch <= "\u9fff")
    punct = sum(1 for ch in line if ch in "(){}[];=<>$\\.")
    if cjk == 0 and len(line) >= 50 and punct >= 2:
        return True
    if cjk <= 1 and len(line) >= 25 and punct >= 2:
        return True

    # High density of code punctuation usually means not article text
    if len(line) >= 80 and punct / max(len(line), 1) >= 0.08:
        return True

    # Remove spaces/punctuation and check for common code tokens
    simplified = re.sub(r"[^a-zA-Z0-9_]+", "", line).lower()
    if not simplified:
        return False

    # Strong JS signals (including common mangled fragments after tag stripping)
    # Examples seen in WeChat pages: "console.in o", "JSON.parse(en S r)", "documen .ge Elemen ById"
    if "console" in simplified:
        return True
    if "jsonparse" in simplified or ("json" in simplified and "parse" in simplified):
        return True
    if "documen" in simplified and ("geelemenbyid" in simplified or "getelementbyid" in simplified):
        return True
    if "documen" in simplified and ("classlis" in simplified or "classlist" in simplified):
        return True
    if "style" in simplified and "display" in simplified:
        return True

    # WeChat/JS pages may lose letters during tag stripping (e.g. 'document' -> 'documen', 'function' -> 'unction')
    if "geelemenbyid" in simplified and "documen" in simplified:
        return True
    if "classlis" in simplified and "documen" in simplified:
        return True
    if "localstorage" in simplified and ("getitem" in simplified or "setitem" in simplified):
        return True

    for kw in _CODE_KEYWORDS:
        kw_s = re.sub(r"[^a-zA-Z0-9_]+", "", kw).lower()
        if kw_s and kw_s in simplified:
            return True

    # Also catch "function/var" variants that might lose a leading character
    if "function" in simplified or "unction" in simplified:
        return True
    if simplified.startswith("var") or simplified.startswith("ar__") or simplified.startswith("ar"):
        return True
    if "reurn" in simplified or "cach" in simplified or "swich" in simplified:
        return True
    # common JS control flow after mangling: "i (cond) {"
    if simplified.startswith("i") and punct >= 2:
        return True

    return False
