"""Typed errors. Messages passed through these types must already be redacted."""

from __future__ import annotations


class GscAgentError(Exception):
    """Base error for expected, user-facing failures."""


class ConfigError(GscAgentError):
    pass


class DatabaseError(GscAgentError):
    pass


class NotAuthorized(GscAgentError):
    retryable = False


class TokenExpired(GscAgentError):
    retryable = False


class PermissionDenied(GscAgentError):
    retryable = False


class QuotaExceeded(GscAgentError):
    retryable = True


class NetworkError(GscAgentError):
    retryable = True


class TransientServerError(GscAgentError):
    retryable = True


class ApiError(GscAgentError):
    retryable = False


class EmptyResult(GscAgentError):
    """A successful API response contained no rows. This is not a failure by itself."""

    retryable = False


class LLMNotConfigured(GscAgentError):
    pass


class RemoteLLMNotAllowed(GscAgentError):
    pass


class DisallowedToolError(GscAgentError):
    pass


class MixedGrainError(GscAgentError):
    """Raised when a caller tries to treat a detailed dataset as the site total."""


RETRYABLE_TYPES = (QuotaExceeded, NetworkError, TransientServerError)
FATAL_SYNC_TYPES = (NotAuthorized, TokenExpired, PermissionDenied)
