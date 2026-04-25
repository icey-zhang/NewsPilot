# coding=utf-8
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

import requests
from NewsPilot.utils.env import env_flag, env_int, get_env


class OpenAICompatClient:
    """
    Minimal OpenAI-compatible Chat Completions client (no extra deps).

    Env vars:
      - NP_LLM_BASE_URL
      - NP_LLM_API_KEY
      - NP_LLM_MODEL (default: gpt-4o-mini)
    """

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: int = 60,
    ):
        self.base_url = (base_url or get_env("NP_LLM_BASE_URL", default="")).rstrip("/")
        self.api_key = api_key or get_env("NP_LLM_API_KEY", default="")
        self.model = model or get_env("NP_LLM_MODEL", default="gpt-4o-mini")
        self.timeout = timeout

        if not self.base_url:
            raise ValueError("缺少 NP_LLM_BASE_URL")
        if not self.api_key:
            raise ValueError("缺少 NP_LLM_API_KEY")

    def chat_json(
        self,
        *,
        system: str,
        user: str,
        json_schema_hint: Optional[Dict[str, Any]] = None,
        temperature: float = 0.2,
    ) -> Dict[str, Any]:
        # base_url 兼容两种写法：
        # - https://api.openai.com
        # - http://host:port/v1
        if self.base_url.endswith("/v1"):
            url = f"{self.base_url}/chat/completions"
        else:
            url = f"{self.base_url}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        schema_text = ""
        if json_schema_hint:
            schema_text = "\nJSON schema hint:\n" + json.dumps(json_schema_hint, ensure_ascii=False)

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user + schema_text},
            ],
        }
        # Some OpenAI-compatible gateways (e.g., LiteLLM) enforce model-group constraints.
        # Example: gpt-5.* and moonshot.* only supports temperature=1.0; passing other values yields 400.
        if temperature is not None:
            temp = float(temperature)
            # gpt-5 and moonshot models only support temperature=1.0
            if (self.model.startswith("gpt-5") or "moonshot" in self.model.lower() or "kimi" in self.model.lower()) and temp != 1.0:
                if env_flag("NP_LLM_DEBUG"):
                    print(f"[LLM][DEBUG] override temperature {temp} -> 1.0 for model={self.model}")
                temp = 1.0
            payload["temperature"] = temp

        max_retries = env_int("NP_LLM_MAX_RETRIES", default=2)
        backoff_ms = env_int("NP_LLM_RETRY_BACKOFF_MS", default=800)

        last_err: Optional[Exception] = None
        for attempt in range(max_retries + 1):
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
                if resp.status_code in (408, 409, 425, 429) or 500 <= resp.status_code <= 599:
                    # retryable HTTP status; respect Retry-After for 429 when provided
                    retry_after = 0.0
                    if resp.status_code == 429:
                        ra = (resp.headers.get("Retry-After") or "").strip()
                        try:
                            retry_after = float(ra)
                        except Exception:
                            retry_after = 0.0
                    resp.raise_for_status()
                resp.raise_for_status()
                data = resp.json()
                break
            except (requests.Timeout, requests.ConnectionError) as e:
                last_err = e
                if attempt >= max_retries:
                    raise
                _sleep_backoff(backoff_ms, attempt)
            except requests.HTTPError as e:
                last_err = e
                status = getattr(e.response, "status_code", None)
                if attempt >= max_retries or not _is_retryable_status(status):
                    raise
                retry_after = 0.0
                try:
                    if status == 429 and e.response is not None:
                        ra = (e.response.headers.get("Retry-After") or "").strip()
                        retry_after = float(ra) if ra else 0.0
                except Exception:
                    retry_after = 0.0
                if retry_after > 0:
                    time.sleep(retry_after)
                else:
                    _sleep_backoff(backoff_ms, attempt)
            except Exception as e:
                last_err = e
                raise
        else:
            # should not happen; defensive
            if last_err:
                raise last_err

        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )

        parsed = _safe_parse_json(content)
        return {
            "model": self.model,
            "raw": content,
            "json": parsed,
        }


def _safe_parse_json(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None

    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        # best-effort remove leading language tag
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].lstrip()

    # try direct parse
    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            return obj
        return {"value": obj}
    except Exception:
        pass

    # try to extract a JSON object
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(cleaned[start : end + 1])
            if isinstance(obj, dict):
                return obj
            return {"value": obj}
        except Exception:
            return None
    return None
def _is_retryable_status(status: Optional[int]) -> bool:
    if status is None:
        return False
    if status in (408, 409, 425, 429):
        return True
    return 500 <= int(status) <= 599


def _sleep_backoff(base_ms: int, attempt: int) -> None:
    # exponential backoff with a small cap
    ms = min(int(base_ms * (2 ** attempt)), 10_000)
    time.sleep(ms / 1000.0)
