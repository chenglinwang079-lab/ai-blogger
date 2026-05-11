"""统一 LLM 调用封装：指数退避重试、429 尊重 Retry-After、content filter 不重试。"""

import json
import time
import logging
from openai import OpenAI, RateLimitError, APIStatusError, APITimeoutError

logger = logging.getLogger(__name__)


def parse_llm_json(response: str, required_keys: list[str]) -> dict:
    """解析 LLM 返回的 JSON，校验必填字段。

    解析失败或缺少字段时抛出 ValueError。
    """
    try:
        result = json.loads(response)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM 返回非法 JSON: {e}") from e

    if not isinstance(result, dict):
        raise ValueError(f"LLM 返回非 dict 类型: {type(result).__name__}")

    missing = [k for k in required_keys if k not in result]
    if missing:
        raise ValueError(f"LLM 返回缺少字段: {missing}")

    return result


def call_llm(
    messages: list[dict],
    *,
    model: str,
    api_key: str,
    base_url: str = "https://api.openai.com/v1",
    max_retries: int = 3,
    retry_delay_seconds: float = 5,
    timeout_seconds: float = 60,
    temperature: float = 0.7,
    response_format: dict | None = None,
) -> str:
    """调用 OpenAI 兼容 API，返回 assistant 文本。

    重试策略：
    - 429: 尊重 Retry-After header，否则指数退避
    - 超时/网络错误: 指数退避
    - content_filter: 不重试，抛出异常
    """
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout_seconds)

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            kwargs = dict(
                model=model,
                messages=messages,
                temperature=temperature,
            )
            if response_format:
                kwargs["response_format"] = response_format

            resp = client.chat.completions.create(**kwargs)
            content = resp.choices[0].message.content
            if content is None:
                raise ValueError("LLM returned empty content")
            return content.strip()

        except RateLimitError as e:
            last_error = e
            retry_after = getattr(e, "retry_after", None) or getattr(e, "response", None)
            if retry_after and hasattr(retry_after, "headers"):
                wait = float(retry_after.headers.get("Retry-After", retry_delay_seconds))
            else:
                wait = retry_delay_seconds * (2 ** attempt)
            if attempt < max_retries:
                logger.warning(f"Rate limited, waiting {wait:.1f}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(wait)
                continue
            raise

        except APIStatusError as e:
            if e.status_code == 400 and "content_filter" in str(e).lower():
                raise  # content filter 不重试
            last_error = e
            if attempt < max_retries:
                wait = retry_delay_seconds * (2 ** attempt)
                logger.warning(f"API error {e.status_code}, retrying in {wait:.1f}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(wait)
                continue
            raise

        except APITimeoutError as e:
            last_error = e
            if attempt < max_retries:
                wait = retry_delay_seconds * (2 ** attempt)
                logger.warning(f"Timeout, retrying in {wait:.1f}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(wait)
                continue
            raise

    raise last_error  # type: ignore[misc]
