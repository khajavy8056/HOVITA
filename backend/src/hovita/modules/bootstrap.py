from __future__ import annotations

from hovita.config.secrets import SecretStore
from hovita.contracts.registry import ProviderRegistry
from hovita.economy.key_rotator import KeyRotator
from hovita.modules.image_gen.providers import ComfyUILocalImageProvider, FalImageEcoProvider
from hovita.modules.llm.providers import (
    ClaudeProLLMProvider,
    FreeLLMAPIFailoverProvider,
    OllamaLocalLLMProvider,
    OpenRouterEcoLLMProvider,
)
from hovita.modules.memory.providers import Mem0MemoryAdapter, ScopedVectorMemoryProvider
from hovita.modules.motion_transfer.providers import WanAnimateLocalProvider
from hovita.modules.publisher.providers import OfficialPlatformPublisher
from hovita.modules.research.providers import (
    SearXNGLocalResearchProvider,
    TavilyEcoResearchProvider,
)
from hovita.modules.social_analytics.providers import (
    EnsembleDataEcoProvider,
    OfficialSocialInsightsProvider,
)
from hovita.modules.stt.providers import FasterWhisperLocalSTTProvider
from hovita.modules.tts.providers import (
    ElevenLabsProTTSProvider,
    KokoroLocalTTSProvider,
    PocketTTSFarsiProvider,
)
from hovita.modules.video_edit.providers import FFmpegLocalVideoEditProvider
from hovita.modules.video_gen.providers import ComfyUIVideoLocalProvider, FalVideoEcoProvider
from hovita.modules.workflow_engine.providers import (
    LangGraphWorkflowProvider,
    N8nBridgeWorkflowProvider,
)


def build_default_registry(
    *,
    rotator: KeyRotator | None = None,
    secret_store: SecretStore | None = None,
) -> ProviderRegistry:
    """Build and populate the 12-module ProviderRegistry using configuration-driven providers."""
    reg = ProviderRegistry()
    kr = rotator or KeyRotator()

    # 1. LLM
    reg.register("llm", OllamaLocalLLMProvider())
    reg.register("llm", FreeLLMAPIFailoverProvider(rotator=kr, secret_store=secret_store))
    reg.register("llm", OpenRouterEcoLLMProvider(secret_store=secret_store))
    reg.register("llm", ClaudeProLLMProvider(secret_store=secret_store))

    # 2. Memory
    reg.register("memory", ScopedVectorMemoryProvider())
    reg.register("memory", Mem0MemoryAdapter())

    # 3. Research
    reg.register("research", SearXNGLocalResearchProvider())
    reg.register("research", TavilyEcoResearchProvider())

    # 4. Image Gen
    reg.register("image_gen", ComfyUILocalImageProvider())
    reg.register("image_gen", FalImageEcoProvider())

    # 5. Video Gen
    reg.register("video_gen", ComfyUIVideoLocalProvider())
    reg.register("video_gen", FalVideoEcoProvider())

    # 6. Motion Transfer
    reg.register("motion_transfer", WanAnimateLocalProvider())

    # 7. TTS
    reg.register("tts", PocketTTSFarsiProvider())
    reg.register("tts", KokoroLocalTTSProvider())
    reg.register("tts", ElevenLabsProTTSProvider())

    # 8. STT
    reg.register("stt", FasterWhisperLocalSTTProvider())

    # 9. Video Edit
    reg.register("video_edit", FFmpegLocalVideoEditProvider())

    # 10. Social Analytics
    reg.register("social_analytics", OfficialSocialInsightsProvider())
    reg.register("social_analytics", EnsembleDataEcoProvider())

    # 11. Publisher
    reg.register("publisher", OfficialPlatformPublisher())

    # 12. Workflow Engine
    reg.register("workflow_engine", LangGraphWorkflowProvider())
    reg.register("workflow_engine", N8nBridgeWorkflowProvider())

    return reg
