"""OpenAI 兼容云 API 的 LLM Provider 实现。

仅通过 HTTPS 调用远端兼容接口，**不拉取、不运行任何本地模型**。
未配置 `LLM_BASE_URL` / `LLM_API_KEY` 时 `is_available()` 返回 False。
"""

import httpx

from smart_farm.config import get_settings
from smart_farm.services.llm.base import LLMProvider

_settings = get_settings()


class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
    ):
        self.base_url = (base_url or _settings.llm_base_url).rstrip("/")
        self.api_key = api_key or _settings.llm_api_key
        self.model = model or _settings.llm_model
        self.timeout = timeout

    def is_available(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)

    def complete(self, prompt: str, *, temperature: float = 0.7) -> str:
        if not self.is_available():
            raise RuntimeError("LLM 未配置（LLM_ENABLED=false 或缺少 LLM_BASE_URL/API_KEY/MODEL）。")
        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]
