"""Sentinel exceptions for the studio LLM pipeline."""


class StudioError(Exception):
    pass


class QuotaExceeded(StudioError):
    pass


class LLMUpstreamError(StudioError):
    """Upstream LLM call failed. `http_status` carries the provider's HTTP
    response code when known (set by providers.py from APIStatusError) so the
    retry classifier can distinguish 429/5xx (retryable) from 4xx (terminal)
    and so the audit log can record it as a structured column."""

    def __init__(self, message: str = "", *, http_status: int | None = None):
        super().__init__(message)
        self.http_status = http_status


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
