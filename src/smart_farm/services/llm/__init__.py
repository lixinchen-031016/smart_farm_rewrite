"""LLM Provider 工厂。

根据配置返回 Provider 实例；默认关闭时不返回可用 Provider。
"""

from smart_farm.config import get_settings
from smart_farm.services.llm.base import LLMProvider
from smart_farm.services.llm.openai_compatible import OpenAICompatibleProvider

_settings = get_settings()


def get_provider() -> LLMProvider | None:
    """返回当前启用的 Provider；未启用返回 None。"""
    if not _settings.llm_enabled:
        return None
    return OpenAICompatibleProvider()
