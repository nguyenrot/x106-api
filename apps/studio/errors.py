"""Sentinel exceptions for the studio LLM pipeline."""


class StudioError(Exception):
    pass


class QuotaExceeded(StudioError):
    pass


class LLMUpstreamError(StudioError):
    pass


class LLMTimeoutError(StudioError):
    pass


class LLMDisabledError(StudioError):
    """No DEEPSEEK_API_KEY configured."""


class LLMOffError(StudioError):
    """Disabled by admin via app_settings."""


class JobNotFound(StudioError):
    pass


class JobInvalidStatus(StudioError):
    pass


class SceneValidationError(StudioError):
    pass


class OutlineValidationError(StudioError):
    """LLM outline (stage 1) failed schema/constraint check."""


class ClusterExpansionError(StudioError):
    """LLM cluster expansion (stage 2) failed validation."""
