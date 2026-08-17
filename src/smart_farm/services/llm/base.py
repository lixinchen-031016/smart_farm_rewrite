"""可插拔 LLM Provider 抽象（替代旧版 Ollama 本地模型）。

设计原则：
- 系统**不再依赖任何本地大模型**（无 ollama、无本地模型拉取）。
- AI 洞察如需启用，统一走 `LLMProvider` 接口；默认实现为 OpenAI 兼容的云 API。
- 默认 `LLM_ENABLED=false`，UI 在未配置时给出降级提示，绝不抛错阻断。
"""

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    @abstractmethod
    def is_available(self) -> bool:
        """返回当前 Provider 是否可调用（已配置且可达）。"""

    @abstractmethod
    def complete(self, prompt: str, *, temperature: float = 0.7) -> str:
        """同步生成文本补全。"""

    def check_model_available(self) -> tuple[bool, list[str]]:
        """兼容旧接口风格：返回 (可用, 可用模型列表)。"""
        return self.is_available(), []
